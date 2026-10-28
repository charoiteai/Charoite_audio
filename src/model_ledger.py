"""Журнал вызовов модели по потребителям (№807): кто сколько минут занял модель.

До №807 время модели знали только у подсказок (`daemon._telemetry`, `model_ms`);
живые минутки, тезисы, разбор графа, досье считались оценкой по константам
(№806: «25–30 мин модели на час встречи» — оценка). Решения №808 и №809 ждут
замера, а не оценки.

Одна строка на один запрос к модели: объект `Call` рождается в транспорте
`llm.py` ДО первой попытки (иначе отказ до ответа 200 — очередь, отказ
соединения — строки не оставлял бы, входной круг DS C1), копит паузы «занято»,
получает тело ответа через `note()` и пишется ровно один раз в `finish()`
(`close` и `__exit__` стрима зовут его оба — DS M1). Токены и время сервера —
из тела Ollama (`prompt_eval_count`, `eval_count`, `total_duration`,
`load_duration`) или `usage` OpenAI-совместимого ответа; у стримов mlx-server и
облака `usage` не приходит — там `null` (S-M2).

Потребитель — явная метка `consumer(...)` у того, кто выбирает работу (у демона
— общий помощник `_collect`, у графа — второй проход). Без метки — имя первого
кадра стека вне транспорта с пометкой `untagged:`: дыры видны, а не подменены
(входной круг S-I1, DS C3). Кадр не хранится — только строка имени (DS M2).
Имя метода (`hint`, `minutes`, …) обёртка передаёт явно: к первому `next()`
стрима её кадра на стеке уже нет (DS C4).

В строке нет ни текста промпта, ни ответа, ни текста ошибки, ни пути встречи —
только имена кода, модель, числа и вид исхода. Файл на день
`logs/model_calls-ГГГГ-ММ-ДД.jsonl`; срок — `llm.ledger_keep_days` из конфига
КОРНЯ, где лежат файлы (умолчание 35 — окно месячного решения №809, `0`
выключает журнал): конфиг в аргументе `LLM` сроком владельца не распоряжается
(финальный Opus, I1), уборка по дате в имени
у писателя: общий ретеншн `logs/` судит по mtime файла и сроку записи звука, он
стёр бы журнал через двое суток простоя (входной круг S-C1, DS I1). Запись —
после снятия аренды, ошибка записи глотается одной строкой на процесс: вызов
модели важнее своего журнала.

Вне журнала сознательно: пробы `llm_health` (один токен), удержание модели
`_hold_tick` (без генерации), дверь векторов и приложение Swift — их запросы
идут мимо транспорта `llm.py` (сверка n807-in1, Haiku C1 понижен).
"""
from __future__ import annotations

import contextlib
import contextvars
import math
import os
import pathlib
import re
import sys
import threading
import time

import charoite_paths
import config_loader
import jsonl_log
import once

LEDGER_KIND = "model_calls"
KEEP_DAYS_DEFAULT = 35
KEEP_DAYS_MAX = 36500           # дальше срок уводит дату отсечки за календарь

_consumer: contextvars.ContextVar[str | None] = contextvars.ContextVar("model_ledger_consumer",
                                                                      default=None)
_DAY_FILE = re.compile(r"model_calls-([0-9]{4}-[0-9]{2}-[0-9]{2})\.jsonl")   # fullmatch: `$` пропускал имя с `\n` (Opus M1)
_sweep_lock = threading.Lock()
_swept: set[tuple[str, str, int]] = set()     # (каталог, день, срок) — уборка раз в день на процесс и срок
_term_lock = threading.Lock()
_terms: dict[str, tuple[tuple, int | None]] = {}   # корень → (отпечаток конфига, срок)


@contextlib.contextmanager
def consumer(name: str):
    """Метка потребителя для вызовов модели внутри блока (и в стримах, которые
    в нём читаются). Нить её не наследует: ставить внутри функции нити."""
    token = _consumer.set(name)
    try:
        yield
    finally:
        _consumer.reset(token)


def caller_name(depth: int = 1) -> str:
    """`модуль.функция` кадра на `depth` выше вызывающего — метка для помощника,
    который сам ничего не выбирает (общий `_collect` демона)."""
    return _frame_name(sys._getframe(depth + 1))


def _frame_name(f) -> str:
    qual = f.f_code.co_qualname.rsplit("<locals>.", 1)[-1]
    mod = f.f_globals.get("__name__", "?")
    if mod == "__main__":                 # скрипт (`graph_updater.py` из конвейера) — по имени файла
        mod = pathlib.Path(f.f_code.co_filename).stem
    return f"{mod}.{qual}"


def _who(skip: frozenset[str]) -> tuple[str, str]:
    tag = _consumer.get()
    if tag:
        return tag, "tag"
    try:
        f = sys._getframe(2)
        while f is not None and f.f_globals.get("__name__") in skip:
            f = f.f_back
        return ("untagged:" + _frame_name(f) if f is not None else "untagged:?"), "stack"
    except Exception:  # noqa: BLE001 — конструктор строки журнала не вправе уронить вызов модели
        return "untagged:?", "stack"


def term(root: pathlib.Path) -> int | None:
    """Срок журнала корня `root` — из его конфига на диске, с кэшем по отпечатку.

    Что писать и что стирать в `logs/` корня, решает только конфиг этого корня:
    словарь, который процесс собрал сам (`scripts/runtime_profile.py`) или
    прочёл на старте (демон), срок владельца не сокращает и журнал не
    выключает; правка `config.yaml` действует у живого демона со следующего
    вызова (финальный Opus, I1). None — конфиг не прочитан: строка пишется,
    но ничего не стирается — срок владельца неизвестен."""
    root = pathlib.Path(root)
    try:    # целиком: исключение отпечатка иначе стоило бы строки, а не уборки (выход n807-out5, DS M3)
        fp = config_loader.fingerprint(root)
        with _term_lock:
            hit = _terms.get(str(root))
        if hit is not None and hit[0] == fp:
            return hit[1]
        n = keep_days(config_loader.load_user_or_example(root))
    except Exception as exc:  # noqa: BLE001 — битый конфиг не вправе уронить вызов модели
        once.say(("model_ledger", "term", type(exc).__name__),
                 f"model_ledger: конфиг не прочитан ({type(exc).__name__}) — журнал пишется, "
                 "прежние дни не стираются", stream=sys.stderr)
        return None
    with _term_lock:
        _terms[str(root)] = (fp, n)
    return n


def keep_days(cfg) -> int | None:
    """Срок журнала из разобранного конфига: число дней, `0` — выключен, None — не понят.

    Умолчание 35 — только когда конфиг понят, а ключа в нём нет. Пустой файл
    (`None`), не словарь, `llm:` не словарём, мусор в значении — «владельца не
    поняли»: строка пишется, прежние дни не стираются (выход n807-out5, три
    головы). Выключает (и стирает прежние дни) только целое `0`: `"0"`, `false`,
    `0.0`, `-1`, `0.5` — не «выключить» (выходы n807-out2…out4)."""
    if not isinstance(cfg, dict) or not isinstance(cfg.get("llm", {}), dict):
        _not_understood("конфиг не словарь" if not isinstance(cfg, dict) else "llm не словарь")
        return None
    raw = cfg.get("llm", {}).get("ledger_keep_days", KEEP_DAYS_DEFAULT)
    n = _count(raw)
    if n is None or n != raw or (n == 0 and not isinstance(raw, int)):   # 0.0 — не выключатель (выход n807-out4)
        _not_understood(f"llm.ledger_keep_days ({type(raw).__name__})")
        return None
    return min(n, KEEP_DAYS_MAX)


def _not_understood(what: str) -> None:
    once.say(("model_ledger", "keep_days", what),
             f"model_ledger: {what} не понят — журнал пишется, прежние дни не стираются; "
             "выключает только число 0", stream=sys.stderr)


def _count(x) -> int | None:
    """Целое неотрицательное число из чужого тела, иначе None (огромное целое
    не бросает OverflowError, как бросил бы `math.isfinite`)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return int(x) if x >= 0 else None


def _tokens(x) -> int | None:
    """Число токенов; больше триллиона — мусор шлюза, не строка в 4 КБ."""
    n = _count(x)
    return None if n is None or n > 10**12 else n


def _seconds_ns(x) -> float | None:
    n = _count(x)
    if n is None or n > 10**18:      # больше 30 лет в наносекундах — мусор; n / 1e9 бросил бы OverflowError
        return None
    return round(n / 1e9, 3)


def _proc() -> str:
    name = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else "?"
    return name[:-3] if name.endswith(".py") else name


class Call:
    """Один запрос к модели — от первой попытки до конца ответа или отказа.
    `skip` — имена модулей транспорта, чьи кадры потребителем не считаются."""

    def __init__(self, root: pathlib.Path, *, engine: str, model: str | None,
                 kind: str, method: str, skip: frozenset[str]) -> None:
        self.root = pathlib.Path(root)
        self.ts = time.time()
        self.t0 = time.monotonic()
        self.consumer, self.source = _who(skip)
        self.engine, self.model, self.kind, self.method = engine, model, kind, method
        self.queue_s = 0.0
        self.first_s: float | None = None
        self.done_s: float | None = None
        self.in_tok: int | None = None
        self.out_tok: int | None = None
        self.server_s: float | None = None
        self.load_s: float | None = None
        self.revived = False
        self.cut: str | None = None
        self._finished = False

    def _now(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def busy(self, seconds: float) -> None:
        """Пауза «занято» (503/429, отказ соединения) перед следующей попыткой."""
        self.queue_s += seconds

    def first(self) -> None:
        """Пришёл первый содержательный кусок ответа."""
        if self.first_s is None:
            self.first_s = self._now()

    def note(self, body) -> None:
        """Конец ответа: тело не-стрима или последняя строка стрима."""
        self.done_s = self._now()
        if not isinstance(body, dict):
            return
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        self.in_tok = _tokens(body.get("prompt_eval_count", usage.get("prompt_tokens")))
        self.out_tok = _tokens(body.get("eval_count", usage.get("completion_tokens")))
        self.server_s = _seconds_ns(body.get("total_duration"))
        self.load_s = _seconds_ns(body.get("load_duration"))
        self.cut = _cut_reason(body)

    def record(self, outcome: str) -> dict:
        rec = {"ts": round(self.ts, 1), "proc": _proc(), "consumer": self.consumer,
               "src": self.source, "method": self.method, "engine": self.engine,
               "model": self.model, "kind": self.kind, "outcome": outcome,
               "wall_s": self.done_s if self.done_s is not None else self._now(),
               "first_s": self.first_s, "queue_s": round(self.queue_s, 3),
               "server_s": self.server_s, "load_s": self.load_s,
               "in_tok": self.in_tok, "out_tok": self.out_tok}
        if self.revived:
            rec["revived"] = True
        if self.cut:
            rec["cut"] = self.cut      # ответ упёрся в потолок токенов — «ok», но недописан
        return rec

    def finish(self, outcome: str) -> None:
        """Записать строку — один раз; ошибка записи вызов модели не роняет."""
        if self._finished:
            return
        self._finished = True
        try:
            keep = term(self.root)
            day = time.strftime("%Y-%m-%d", time.localtime(self.ts))
            path = charoite_paths.log_path(self.root, LEDGER_KIND, part=day)
            if keep == 0:
                # выключенный журнал не пишет и не оставляет прежних дней: иначе
                # у них не осталось бы ни одного механизма удаления (выход n807-out1)
                _sweep(path.parent, 0, day)
                return
            jsonl_log.append_record(path, self.record(outcome))
            if keep is not None:
                _sweep(path.parent, keep, day)
        except Exception as exc:  # noqa: BLE001 — журнал не вправе уронить генерацию
            once.say(("model_ledger", type(exc).__name__),
                     f"model_ledger: журнал вызовов модели не пишется ({type(exc).__name__}) — "
                     "вызовы модели идут без учёта", stream=sys.stderr)


def _cut_reason(body: dict) -> str | None:
    """Почему генерация кончилась, если не сама: `done_reason` Ollama,
    `finish_reason` OpenAI-совместимого ответа (у SSE его кладёт `_sse`)."""
    reason = body.get("done_reason") or body.get("finish_reason")
    choices = body.get("choices")
    if not reason and isinstance(choices, list) and choices and isinstance(choices[0], dict):
        reason = choices[0].get("finish_reason")
    return reason if reason == "length" else None


def _sweep(logs: pathlib.Path, keep: int, today: str) -> None:
    """Стереть дни старше срока — по дате в имени, раз в день на процесс;
    `keep == 0` — все дни."""
    if keep:    # выключенный журнал убирает каждый раз: 0 → N → 0 за день оставил бы день N (выход n807-out5)
        key = (str(logs), today, keep)   # новый срок с диска — новая уборка в тот же день
        with _sweep_lock:
            if key in _swept:
                return
            _swept.add(key)
    if keep:    # jsonl_log (base) создаёт каталог без 0700; выключенный журнал каталог не заводит (выход n807-out3)
        with contextlib.suppress(OSError):   # права — попутно; уборка по имени — главное (выход n807-out4)
            charoite_paths.secure_dir(logs)
    cutoff = "9999" if keep == 0 else time.strftime("%Y-%m-%d", time.localtime(time.time() - keep * 86400))
    try:
        names = os.listdir(logs)
    except OSError:
        return
    for name in names:
        m = _DAY_FILE.fullmatch(name)
        if m and m.group(1) < cutoff:
            with contextlib.suppress(OSError):
                (logs / name).unlink()
