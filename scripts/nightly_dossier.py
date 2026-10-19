#!/usr/bin/env python3
"""Ночная сборка досье по темам графа.

    .venv/bin/python scripts/nightly_dossier.py               # текущий граф, инкрементально
    .venv/bin/python scripts/nightly_dossier.py --full        # пересобрать всё
    .venv/bin/python scripts/nightly_dossier.py --all-graphs  # все графы vault
    .venv/bin/python scripts/nightly_dossier.py --dry         # показать план, не писать
    .venv/bin/python scripts/nightly_dossier.py --budget-s 600  # бюджет шага в секундах
    .venv/bin/python scripts/nightly_dossier.py --find "запрос"  # проверить, что найдётся

Инкрементальность: у каждого досье в frontmatter лежит отпечаток состава
(список источников + их mtime). Совпал — тема не менялась, модель не зовём.

Все графы — одна очередь: по одной теме из каждого графа с работой раньше
вторых тем, внутри графа — несобранные, потом давно собранные. Шаг держит
бюджет по времени (`--budget-s`, по умолчанию 40 минут, у `--full` без него —
без бюджета) и под ночным потолком оставляет время шагам после себя
(`live_gate.step_budget`); остаток очереди — следующей ночью, индекс каждого
графа при этом полный.

Запускается из nightly.sh ПОСЛЕ tier3 — чтобы досье собирались по уже
причёсанным ядрам, без дублей.
"""
from __future__ import annotations

import argparse
import contextlib
import pathlib
import signal
import sys
import time
from datetime import date, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import charoite_paths  # noqa: E402
from charoite_graph import dossier  # noqa: E402
from charoite_schema import CHAROITE  # noqa: E402
import file_locks  # noqa: E402
import graphs  # noqa: E402
import live_gate  # noqa: E402
from charoite_graph import safe_write  # noqa: E402
from charoite_paths import code_root, resolve_root  # noqa: E402
from config_loader import load_user_or_example  # noqa: E402

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = code_root(__file__)


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте."""
    return resolve_root(__file__)


def cfg() -> dict:
    return load_user_or_example(_root()) or {}


def default_graph(c: dict) -> pathlib.Path:
    return graphs.graph_dir(c) or pathlib.Path.cwd()


def all_graphs() -> list[pathlib.Path]:
    """Графы всех vault-ов, а не только iCloud: настроенный graph_dir вне
    iCloud (`~/Documents/Charoite/Work` из примера конфига) раньше молча
    оставался без досье — шаг ночи «ok», собрано 0 (аудит 17.08). Единая
    точка — src/graphs.py, как у tier3 и брифа."""
    return graphs.all_graphs("Ядра")


def _index_entry_from_disk(theme: str, members: list[str], loaded: dossier.DossierLoad,
                           fp: str, today: str) -> dict:
    """Запись индекса для темы, которую этот прогон не пересобирал: досье
    на диске живо — в индексе оно должно остаться (отпечаток — фактический)."""
    return {
        "тема": theme, "файл": f"{CHAROITE.dossier_dir}/{theme}.md",
        "источников": len(members),
        "собрано": _собрано(loaded) or today,
        "отпечаток": dossier.read_fingerprint(loaded) or fp,
        "ключи": dossier.keywords(theme + " " + " ".join(members)),
    }


class _DossierReads:
    """Один снимок на путь за прогон, один лог и учёт повреждённых путей."""

    def __init__(self):
        self.loaded: dict[pathlib.Path, dossier.DossierLoad] = {}
        self.damaged: set[pathlib.Path] = set()

    def load(self, path: pathlib.Path) -> dossier.DossierLoad:
        if path not in self.loaded:
            loaded = dossier.load_dossier(path)
            self.loaded[path] = loaded
            if loaded.state == "damaged":
                self.damaged.add(path)
                print(f"  ⚠️ битое досье: {path} — UTF-8 прочитан с заменой")
        return self.loaded[path]

    def reload(self, path: pathlib.Path) -> dossier.DossierLoad:
        """Свежий снимок пути, если файл правили после скана."""
        self.loaded.pop(path, None)
        self.damaged.discard(path)
        return self.load(path)


def _manual_for_rebuild(theme: str, loaded: dossier.DossierLoad) -> tuple[bool, str | None, int]:
    """Решение ДО модели: можно ли сохранить единственный авторский текст.

    Третий элемент — отказ для сводки; повреждение не означает отказ модели.
    """
    if loaded.state == "unreadable":
        print(f"  ✗ {theme}: прежнее досье не прочитано — не трогаю")
        return False, None, 1
    text = loaded.text or ""
    if loaded.state == "damaged":
        section = dossier.KEEP_RE.search(text)
        # заголовок раздела — литерал регулярки, поэтому знак замены во всём совпадении означает знак замены в тексте раздела
        if section is None or "\ufffd" in section.group():
            print(f"  ✗ {theme}: Правки автора повреждены или не найдены — не трогаю")
            return False, None, 0
    return True, dossier.preserve_manual(text), 0


def generate(theme: str, members: list[str], files: dict, c: dict,
             temperature: float = 0.2) -> str:
    """Сводка локальной моделью. Думать не просим: на структурной задаче
    рассуждение съедает бюджет генерации и документ выходит беднее."""
    sys.path.insert(0, str(CODE / "src"))
    from llm import LLM  # noqa: PLC0415

    llm = LLM(c)
    prompt = dossier.build_prompt(theme, members, files, schema=CHAROITE)
    model = (c.get("llm") or {}).get("model")
    out = []
    for chunk in llm.stream(
            prompt, model=model, think=False,
            temperature=temperature, num_predict=1800,
            system=("Ты составляешь фактические справки по документам. "
                    "Отвечаешь только текстом справки в заданном формате, "
                    "без обращений к читателю и без вопросов.")):
        out.append(chunk)
    return "".join(out).strip()


LOCK_WAIT = 5 * 60          # дольше держит только зависший сосед


@contextlib.contextmanager
def _graph_lock(graph: pathlib.Path, wait: float = LOCK_WAIT):
    """Общий замок пишущих в граф: разбор встречи, облачная ревизия — и мы.

    Локальная сборка досье замок не брала вовсе, хотя переписывает те же
    файлы и индекс: сосед мог читать наполовину записанную папку, а его
    сверка — принять наши правки за чужие (аудит графа 26.08, Codex).
    Берём коротко, на одну запись: держать его весь прогон (до часа) значило
    бы заставить ждать разбор только что закончившейся встречи.
    """
    try:
        lock_dir = charoite_paths.secure_dir(
            charoite_paths.graph_backups(graph, "cloud_backup", root=_root()).parent)
    except OSError as e:
        print(f"  замок графа не взять ({e}) — не пишу")
        yield False
        return
    with file_locks.graph_lock(lock_dir, wait) as taken:
        yield taken


# Бюджет шага по времени вместо потолка в штуках (№621): 12 тем за ночь на
# все графы разом уходили большому графу, а пять малых стояли без свежих
# досье с июля. Секунды, а не темы: ночь меряется часами. 2400 — 40 минут
# окна, по соглашению с индексом поиска (`MEMORY_INDEX_BUDGET` в nightly.sh).
DEFAULT_BUDGET_S = 2400
# Оценка темы, пока этот прогон не собрал ни одной: замер 18.10 — 37,6 с.
NEED_START_S = 40.0

# Шов часов: бюджет тесты судят подменённым временем, а не сном (как в mutate_check)
clock = time.monotonic

# Классы кандидата: досье нет / отпечаток разошёлся / без изменений (только --full)
NEW, CHANGED, SAME = 1, 2, 3
_CLASS_WORD = {NEW: "новое", CHANGED: "изменилось", SAME: "без изменений"}


class _GraphRun:
    """Один граф за прогон: снимок источников, индекс и счётчики причин.

    Индекс — карта ВСЕХ досье на диске, а не только тех, что посетил прогон:
    темы сверх лимита, с отказом и не успевшие за ночь раньше выпадали из
    _index/_ИНДЕКС (аудит 17.08). Поэтому `entries` заполняется со скана
    записями с диска, а собранная тема заменяет свою запись.
    """

    def __init__(self, graph: pathlib.Path, files: dict, cl: dict, today: str, stamp: str):
        self.graph, self.folder = graph, graph / CHAROITE.dossier_dir
        self.files, self.cl, self.today, self.stamp = files, cl, today, stamp
        self.reads = _DossierReads()
        self.entries: dict[str, dict] = {}
        self.pending = 0          # кандидатов графа ещё в очереди
        self.busy = False         # сосед держал замок дольше LOCK_WAIT
        self.indexed = False
        self.built = self.skipped = self.отказы = 0
        # Раздельно, иначе ночь с занятым графом выглядит как «всё без
        # изменений» (круг-2 по PR #438, DS Minor 9).
        self.unchanged = self.over_limit = self.locked = self.late = 0
        self.changed = self.broken_manual = 0

    def keep(self, theme: str, members: list[str], loaded: dossier.DossierLoad, fp: str) -> None:
        """Неприменённая пересборка оставляет прежнее досье в индексе."""
        if loaded.state != "missing":
            self.entries[theme] = _index_entry_from_disk(theme, members, loaded, fp, self.today)

    def write_index(self, dry: bool) -> None:
        """Один раз за прогон: как только у графа кончились кандидаты, а для
        остальных — в `finally` прогона. Без замка: write_index пишет через
        tmp+replace, полуфайла не будет, а ждать соседа значило бы оставить
        свежие досье невидимыми для поиска до следующей ночи (круг-2 по PR
        #438, DS Important 3)."""
        if self.indexed:
            return
        self.indexed = True
        if not dry and self.entries:
            dossier.write_index(self.folder, list(self.entries.values()))

    def done(self, dry: bool) -> None:
        self.pending -= 1
        if self.pending == 0:
            self.write_index(dry)

    def result(self) -> dict:
        return {"граф": self.graph.name, "тем": len(self.cl), "собрано": self.built,
                "пропущено": self.skipped, "отказы": self.отказы, "битые": len(self.reads.damaged),
                "без_изменений": self.unchanged, "сверх_лимита": self.over_limit,
                "занят": self.locked, "не_успели": self.late,
                "правки_повреждены": self.broken_manual, "досье_изменилось": self.changed}

    def report(self) -> None:
        if self.skipped:
            print(f"  {self.graph.name}: пропущено {self.skipped}: без изменений {self.unchanged}, "
                  f"сверх лимита {self.over_limit}, граф занят {self.locked}, "
                  f"не успели за ночь {self.late}, правки автора повреждены {self.broken_manual}, "
                  f"досье изменилось {self.changed}")


class Candidate:
    """Тема к сборке. Не dataclass: тесты грузят скрипт без записи в
    sys.modules, а dataclass ищет там свой модуль для строковых аннотаций."""

    def __init__(self, g: _GraphRun, theme: str, members: list[str], fp: str, cls: int,
                 собрано: str, loaded: dossier.DossierLoad, mtime: tuple[int, int] | None):
        self.g, self.theme, self.members, self.fp = g, theme, members, fp
        self.cls, self.собрано, self.loaded, self.mtime = cls, собрано, loaded, mtime


def _scan(graph: pathlib.Path, full: bool, today: str, stamp: str) -> tuple[_GraphRun, list[Candidate]]:
    files, backlinks = dossier.scan(graph, schema=CHAROITE)
    cl = dossier.clusters(files, backlinks, schema=CHAROITE) if files else {}
    g = _GraphRun(graph, files, cl, today, stamp)
    found = []
    for theme, members in cl.items():
        path = g.folder / f"{theme}.md"
        fp = dossier.fingerprint(members, files)
        loaded = g.reads.load(path)
        g.keep(theme, members, loaded, fp)
        same = dossier.read_fingerprint(loaded) == fp and loaded.state != "missing"
        if same and not full:
            g.skipped += 1
            g.unchanged += 1
            continue
        cls = NEW if loaded.state == "missing" else SAME if same else CHANGED
        found.append(Candidate(g, theme, members, fp, cls, _собрано(loaded), loaded, safe_write.stat_snapshot(path)))
    g.pending = len(found)
    return g, found


def _graph_key(c: Candidate) -> tuple:
    """Порядок внутри графа: несобранные, потом давно собранные, потом крупные.
    Прежняя сортировка только по размеру отдавала все слоты ночи большим
    «несвежим» кластерам, а новые маленькие темы стояли в хвосте (аудит 26.08)."""
    return (c.cls, c.собрано, -len(c.members), c.theme.casefold(), c.theme)


def queue_order(candidates: list[Candidate]) -> list[Candidate]:
    """Одна очередь на все графы: по одной теме из каждого графа с работой
    раньше вторых тем. Круг — позиция темы в своём графе по `_graph_key`, и он
    старше класса: при классе впереди все новые темы большого графа шли бы
    раньше любой темы малого (вход in3 №621, DS C1 — первая тема малого графа
    на позиции 600)."""
    groups: dict[int, list[Candidate]] = {}
    for c in candidates:
        groups.setdefault(id(c.g), []).append(c)
    keyed = []
    for group in groups.values():
        for rnd, c in enumerate(sorted(group, key=_graph_key)):
            # круг и граф различают любые две темы: хвост ключа графа не нужен
            keyed.append(((rnd, c.cls, c.собрано, -len(c.members),
                           c.g.graph.name.casefold(), str(c.g.graph)), c))
    return [c for _, c in sorted(keyed, key=lambda kc: kc[0])]


def _build(cand: Candidate, c: dict, lock_wait=lambda: LOCK_WAIT) -> float | None:
    """Собрать одну тему. Возвращает секунды генерации — цену темы для
    `need`, без ожидания замка; None — модель не звали. Собрана ли тема —
    счётчик `g.built` (флаг рядом с ним мутанты #819 не видели); `lock_wait` —
    сколько ждать замок, считается в момент записи, после генерации."""
    g, theme, members, fp = cand.g, cand.theme, cand.members, cand.fp
    path = g.folder / f"{theme}.md"
    loaded = cand.loaded
    if safe_write.stat_snapshot(path) != cand.mtime:
        # Досье правили после скана (автор в Obsidian): снимок устарел, иначе
        # сверка после генерации выбросит её как «изменилось за время
        # генерации» (вход in3 №621, DS M2).
        loaded = g.reads.reload(path)
        g.keep(theme, members, loaded, fp)
    allowed, manual, failures = _manual_for_rebuild(theme, loaded)
    if not allowed:
        g.отказы += failures
        if failures == 0:
            # Отказ из-за повреждённых «Правок автора» — это пропуск темы,
            # а не молчание модели: тема уйдёт на следующую ночь.
            g.skipped += 1
            g.broken_manual += 1
        return None
    g0 = clock()
    body = ""
    for temperature in (0.2, 0.05):          # вторая попытка чуть холоднее
        try:
            body = dossier.trim_to_format(
                generate(theme, members, g.files, c, temperature=temperature))
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ {theme}: модель не ответила ({type(e).__name__}: {e})")
            body = ""
            break          # отказ считается ниже, в ветке `if not body`
        if dossier.looks_valid(body):
            break
        print(f"  … {theme}: ответ не по формату (температура {temperature})")
        body = ""
    if not body:
        # Брак дважды подряд — тот же отказ, что и исключение: тема
        # осталась без разбора. Раньше он не считался, ночь при
        # «Принято, что дальше?» на всех темах выходила с кодом 0 и
        # статусом «ok» (аудит 17.08). Прежнее досье в индексе остаётся.
        g.отказы += 1
        return clock() - g0
    gen_s = clock() - g0

    text = dossier.render(theme, body, members, g.files, fp, g.today)
    if manual:
        text = text.replace("## Правки автора\n\n—\n", f"## Правки автора\n\n{manual}\n")

    copy = None
    wait = lock_wait()
    with _graph_lock(g.graph, wait) as taken:
        if not taken:
            # Занятость — свойство графа на прогон, а не темы: без отметки
            # каждая его тема ждала бы замок пять минут после генерации, и
            # бюджет ушёл бы на ожидание (вход in3 №621, Haiku C1, Sonnet I3).
            print(f"  ⏸ {g.graph.name}: замок графа не взят за {wait:.0f} с "
                  "— его темы уйдут на следующую ночь")
            # Ожидание в ноль — это вышедшая ночь или бюджет, а не сосед:
            # хвост с верной причиной отдаст _drive (выход out3 №621).
            g.busy = wait > 0
            g.skipped += 1
            g.locked += 1
            return gen_s
        # Снимок — до сверочного чтения: правка автора между сверкой и
        # replace не затирается, запись идёт через гейт `expect` (выход out2
        # №621, DS C1; окно было и в базе).
        snap = safe_write.stat_snapshot(path)
        # Второе чтение только сверяет и ничего не решает — решение 3 («одно чтение на тему») держится на снимке `loaded`, а в `битые` попадает только `reads`.
        fresh = dossier.load_dossier(path)
        if fresh.state != loaded.state or fresh.text != loaded.text:
            print(f"  ⏸ {theme}: досье изменилось за время генерации — "
                  "тема уйдёт на следующую ночь")
            g.skipped += 1
            g.changed += 1
            g.keep(theme, members, fresh, fp)
            return gen_s
        if snap is None and fresh.state != "missing":
            # stat не прочёл файл, который читается: без снимка гейт слеп,
            # и «не сосчитать» стало бы «файла нет» (выход out3 №621, DS I1).
            print(f"  ⏸ {theme}: досье не дало снимок перед записью — тема уйдёт на следующую ночь")
            g.skipped += 1
            g.changed += 1
            g.keep(theme, members, fresh, fp)
            return gen_s
        # копия прежнего досье: пересборка сохраняла только «Правки автора» и
        # стирала тело с редактурой облачной ревизии без единой копии
        # (аудит 13.09, GLM C1); тот же .backup/, что у ревизии. Не вышла
        # копия — тему не перезаписываем, остальные не теряем (DS I2 / GLM I1 по #561)
        try:
            copy = dossier.backup(g.folder, g.stamp, path)
        except OSError as e:
            print(f"  ✗ {theme}: копия прежнего досье не сделана ({e}) — не перезаписываю")
            g.отказы += 1
            return gen_s
        # safe_write: во временный и replace, с переносом прав и меток файла (DS M2 по #561)
        if not safe_write.write_text(path, text, expect=snap, expect_absent=snap is None):
            print(f"  ⏸ {theme}: досье изменилось перед записью — тема уйдёт на следующую ночь")
            g.skipped += 1
            g.changed += 1
            g.keep(theme, members, dossier.load_dossier(path), fp)
            return gen_s
    g.built += 1
    if copy is not None:
        print(f"  копия прежнего досье: {CHAROITE.dossier_dir}/.backup/{g.stamp}/")
    print(f"  ✓ {g.graph.name} · {theme}: {len(members)} источников, {len(body)} зн., {gen_s:.0f}с")
    g.entries[theme] = {
        "тема": theme, "файл": f"{CHAROITE.dossier_dir}/{theme}.md",
        "источников": len(members), "собрано": g.today, "отпечаток": fp,
        "ключи": dossier.keywords(theme + " " + " ".join(members) + " " + body),
    }
    return gen_s


def _tail(queue: list[Candidate], reason: str, dry: bool) -> None:
    """Остаток очереди — на завтра. Записи с диска у этих тем уже в индексе:
    голый break отдавал write_index посещённый префикс, и _index/_ИНДЕКС
    терял темы — регресс бага 17.08 (круг по PR #363, GLM)."""
    print(f"  ⏹ {reason} — остальные темы завтра")
    for cand in queue:
        cand.g.skipped += 1          # сводка не врёт про хвост (круг-2, DS)
        cand.g.late += 1
        cand.g.done(dry)


def _drive(queue: list[Candidate], c: dict, *, full: bool, dry: bool,
           limit: int | None, budget_s: float | None, started: float) -> int:
    """Пройти очередь; вернуть число попыток с моделью — знаменатель порога
    «модель молчала» (выход out1 №621: «занят» и «изменилось» тоже попытки)."""
    attempts = 0
    built = 0                 # --limit — потолок прогона, а не графа (аудит 26.08, Codex)
    spent: list[float] = []   # все попытки с моделью, и отказы тоже (вход in3, DS M1)
    budget = budget_s   # решён в main: --full без бюджета — None (№749)
    for i, cand in enumerate(queue):
        g = cand.g
        if dry:
            print(f"  [план] {g.graph.name} · {cand.theme}: {len(cand.members)} источников"
                  f" ({_CLASS_WORD[cand.cls]})")
            g.built += 1
            g.done(dry)
            continue
        if g.busy:
            g.skipped += 1
            g.locked += 1
            g.done(dry)
            continue
        if not full and limit is not None and built >= limit:
            g.skipped += 1
            g.over_limit += 1
            g.done(dry)
            continue
        need = sum(spent) / len(spent) if spent else NEED_START_S
        if not live_gate.fits(budget, started, need, clock()):
            _tail(queue[i:], "бюджет шага досье вышел", dry)
            break
        # Утренняя встреча посреди хвоста ночи: пока суфлёр слушает, модель
        # его — досье подождёт, но не дольше остатка бюджета. Окно — одна
        # дверь у владельца гейта, потолок ночи внутри.
        gate = {} if budget is None else {"default": budget - (clock() - started)}
        if not live_gate.night_window_open(_root(), what="досье", **gate):
            _tail(queue[i:], "время ночного прогона вышло", dry)
            break
        if not live_gate.fits(budget, started, need, clock()):
            _tail(queue[i:], "бюджет шага досье вышел, пока ждали конца встречи", dry)
            break
        # Замок ждёт не дольше остатка бюджета: пять минут соседа на последней
        # теме перебирали бы бюджет на всё ожидание (выход out1 №621, Sonnet I2).
        def lock_wait() -> float:
            # и потолок ночи — у его владельца (выход out2 №621, Sonnet I1)
            return live_gate.night_wait_cap(
                LOCK_WAIT if budget is None else min(LOCK_WAIT, budget - (clock() - started)))
        refused, before = g.отказы, g.built
        gen_s = _build(cand, c, lock_wait)
        called = gen_s is not None   # флаг «звали модель» дублировал это (мутанты #819)
        if called:                 # цена темы — генерация, без ожидания замка (Haiku I1)
            spent.append(gen_s)
        attempts += called or g.отказы > refused   # нечитаемое досье — отказ без модели
        built += g.built - before
        g.done(dry)
    return attempts


class RunResult:
    """Итог прогона: графы, попытки с моделью, графы с упавшим сканом."""

    def __init__(self):
        self.graphs: list[_GraphRun] = []
        self.attempts = 0
        self.scan_failed: list[str] = []


def run_all(paths: list[pathlib.Path], c: dict, *, full: bool, dry: bool,
            limit: int | None = None, budget_s: float | None = None,
            started: float | None = None) -> "RunResult":
    """Все графы одной очередью: скан всех, порядок `queue_order`, сборка до
    бюджета. Индекс каждого графа пишется, даже если прогон оборвался."""
    started = clock() if started is None else started
    today = date.today().isoformat()
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")   # штамп копий .backup/, с секундами как у ревизии
    out = RunResult()
    candidates: list[Candidate] = []
    try:
        for path in paths:
            # Граф — единица отказа: прежний цикл по графам успевал собрать
            # графы до сломанного, а общий скан без этой границы отменял бы
            # работу всех (выход out1 №621, Sonnet I1, DS I1).
            try:
                g, found = _scan(path, full, today, stamp)
            except Exception as e:  # noqa: BLE001
                print(f"⚠️ скан {path.name} не удался ({type(e).__name__}: {e}) — граф пропущен")
                out.scan_failed.append(path.name)
                continue
            out.graphs.append(g)
            candidates += found
            print(f"скан {path.name}: тем {len(g.cl)}, к сборке {len(found)}")
        out.attempts = _drive(queue_order(candidates), c, full=full, dry=dry, limit=limit,
                              budget_s=budget_s, started=started)
    finally:
        # Исключение или SIGTERM посреди очереди не оставляет графы со старым
        # индексом (вход in3 №621, Sonnet I2, DS I2).
        for g in out.graphs:
            g.write_index(dry)
    return out


def run(graph: pathlib.Path, c: dict, full: bool, dry: bool, limit: int | None,
        budget_s: float | None = None) -> dict:
    """Один граф — та же очередь из одного графа."""
    result = run_all([graph], c, full=full, dry=dry, limit=limit, budget_s=budget_s)
    if not result.graphs:
        raise RuntimeError(f"скан графа {graph} не удался")
    g = result.graphs[0]
    g.report()
    return g.result()


def _собрано(source: pathlib.Path | dossier.DossierLoad) -> str:
    import re
    loaded = dossier.load_dossier(source) if isinstance(source, pathlib.Path) else source
    m = re.search(r"^собрано:\s*(\S+)", (loaded.text or "")[:400], re.M)
    try:
        return date.fromisoformat(m.group(1)).isoformat() if m else ""
    except ValueError:
        return ""


def resolve_budget(budget_s: float | None, full: bool) -> float | None:
    """Бюджет прогона — одно место, `_drive` о `--full` не знает.

    Не задан: `--full` — без бюджета (ручная пересборка «всего»), иначе
    умолчание. Ноль и минус — не «ничего не делать», а забытое значение
    (выход out3 №621), и с `--full` тоже. Под ночным потолком бюджет
    урезается так, чтобы шагам после досье хватило ночи: воскресный `--full`
    без бюджета доходил до потолка, и хвост ночи уходил в «(поздно)» (№749).
    """
    if budget_s is None:
        budget_s = None if full else DEFAULT_BUDGET_S
    elif budget_s <= 0:
        budget_s = DEFAULT_BUDGET_S
    return live_gate.step_budget(budget_s)


def _terminate(signum, frame):  # noqa: ARG001
    """SIGTERM потолка ночи — в SystemExit, чтобы `finally` прогона дописал
    индексы графов (вход in3 №621, Sonnet I2)."""
    raise SystemExit(143)


def main() -> int:
    charoite_paths.harden_umask()   # досье по темам встреч — только владельцу (№385)
    started = clock()   # бюджет — со старта, скан всех графов внутри него (вход in3, DS I3)
    ap = argparse.ArgumentParser(description="Ночная сборка досье по темам графа")
    ap.add_argument("--graph", help="путь к графу")
    ap.add_argument("--all-graphs", action="store_true", help="все графы vault")
    ap.add_argument("--full", action="store_true",
                    help="пересобрать все темы (без --budget-s — без бюджета)")
    ap.add_argument("--dry", action="store_true", help="показать план, не писать")
    ap.add_argument("--budget-s", type=float, default=None,
                    help=f"бюджет шага в секундах (по умолчанию {DEFAULT_BUDGET_S}); "
                         "остаток очереди — на завтра")
    ap.add_argument("--limit", type=int, default=None, help="ручной потолок тем за прогон")
    ap.add_argument("--find", help="проверить, что найдётся по запросу")
    args = ap.parse_args()
    args.budget_s = resolve_budget(args.budget_s, args.full)
    c = cfg()

    if args.find:
        graph = pathlib.Path(args.graph).expanduser() if args.graph else default_graph(c)
        hits = dossier.lookup(graph / CHAROITE.dossier_dir, args.find)
        if not hits:
            print("досье не найдено — поиск пойдёт по графу")
            return 0
        for h in hits:
            print(f"{h['счёт']:>5}  {h['тема']}  ({h['источников']} источников, {h['собрано']})")
            print(f"       {h['файл']}")
        return 0

    graph_paths = all_graphs() if args.all_graphs else [
        pathlib.Path(args.graph).expanduser() if args.graph else default_graph(c)]
    present = []
    for g in graph_paths:
        if g.is_dir():
            present.append(g)
        else:
            print(f"{g}: нет такой папки")

    signal.signal(signal.SIGTERM, _terminate)
    result = run_all(present, c, full=args.full, dry=args.dry, limit=args.limit,
                     budget_s=args.budget_s, started=started)
    total = отказов = 0
    for g in result.graphs:
        r = g.result()
        print(f"=== {r['граф']}: тем: {r['тем']}, собрано: {r['собрано']}, пропущено: {r['пропущено']}, битые: {r['битые']}"
              + (f", отказов модели: {r['отказы']}" if r["отказы"] else ""))
        g.report()
        total += r["собрано"]
        отказов += r["отказы"]

    print(f"итого собрано досье: {total}")
    попыток = result.attempts
    if отказов:
        print(f"отказов модели: {отказов} из {попыток} попыток")
    # Молчащая модель — это не успех.
    #
    # 12.08 локальный сервер лёг посреди прогона: 258 тем ушли без разбора,
    # а шаг закончился нулём — и статус ночи получился «ok». Досье не
    # собрались, граф черствел, и заметить это можно было только вручную
    # читая лог. Единичный отказ случается и на живой модели, поэтому порог,
    # а не «любой отказ». Знаменатель — попытки, а не темы: бюджет пускает
    # 40–80 попыток из ~950 тем, и порог по темам не срабатывал бы вовсе
    # (вход in3 №621, Sonnet I1).
    if result.scan_failed:   # до порога: код 2 не прячет пропавший граф (выход out2 №621)
        print(f"⚠️ скан не удался: {', '.join(result.scan_failed)}")
    if отказов >= max(3, попыток // 10):
        return 4 if result.scan_failed else 2   # 4 — и то и другое (выход out3 №621)
    return 1 if result.scan_failed else 0


if __name__ == "__main__":
    sys.exit(main())
