"""Решающий гейт: стоит ли будить модель ради реплики — пониманием, а не списком.

Сегодня вопрос на ⚡ отбирают структурные проверки `question_filter`: «?» или
вопросное слово в начале, затем длина и повтор. Всё, что прошло, будит модель;
модель, не найдя вопроса, отвечает «уточните» — такой отказ в нить не идёт, но
секунды модели (и квота, если включено облако) уже потрачены. Правило проекта
запрещает лечить это списком фраз, а LLM-судья на каждую реплику стоит столько
же, сколько сам ответ.

Решающая модель (класс Jev/Laya: энкодер и голова решения, один проход без
генерации) отвечает «вопрос это или нет» за десятки миллисекунд и отдаёт
уверенность. Уверенность — ключ: гейт вправе молчать за модель только там, где
уверен, остальное уходит модели как сегодня. Это каскад «принять, если уверен,
иначе эскалировать» (arXiv 2609.26550).

Модуль — исследовательский шаг, и за демона он ничего не решает. Режим один —
ТЕНЬ (`sufler.decision_gate_shadow: true`): вердикт считается параллельно
генерации ⚡ и печатается в err-лог рядом с исходом — ответила модель или
отказалась. Контента в строке нет: метка, уверенность, задержка и ничего,
выведенного из текста. err-лог — про состояние, не про разговор (как `hint-pulse`). Включать
ли гейт по-настоящему и с каким порогом, решает `scripts/gate_bench.py` по
накопленным строкам, а не догадка.

Бэкенды по убыванию предпочтения:

* обученная голова — `models/decision/question_gate/` (`model.onnx`,
  `tokenizer.json`, `labels.json` с температурой калибровки): дистиллят,
  обученный на своих встречах, один проход;
* zero-shot через NLI, которая уже лежит в `models/nli/` (`nli.py`): две
  гипотезы, нормировка следования. Ни новых пакетов, ни скачиваний, но
  уверенность не откалибрована — ровно поэтому только тень.

Нет ни того, ни другого — фабрика честно отказывает строкой `refused`, демон
работает как раньше.
"""
from __future__ import annotations

import dataclasses
import json
import math
import pathlib
import sys
import threading
import time
from typing import Callable, Mapping

import nli
from charoite_paths import MODELS_DIR, resolve_root

#: Метки гейта. «ask» — будить модель, «skip» — реплика вопроса не несёт.
LABELS = ("ask", "skip")

#: Исходы ⚡, против которых тень пишет вердикт. «refusal» — модель ответила
#: «вопроса не вижу» (`question_filter.is_refusal`), «failed» — ответа нет.
OUTCOMES = ("answered", "refusal", "failed")

#: Гипотезы zero-shot. Обе утвердительные: NLI заметно хуже судит отрицания
#: («это НЕ вопрос»), чем выбор между двумя описаниями. Формулировки —
#: предмет замера (`gate_bench.py eval --hyp-ask/--hyp-skip`), а не догма.
HYPOTHESES: Mapping[str, str] = {
    "ask": "Собеседник задаёт вопрос и ждёт ответа.",
    "skip": "Собеседник переспрашивает, думает вслух или обрывает фразу.",
}

#: Ключ конфига. Булев, а не «off/shadow/on»: YAML 1.1 читает голое off как
#: False, и строковый режим молча становился бы булевым.
SHADOW_KEY = "decision_gate_shadow"

#: Начало строки тени в err-логе. Формат строит `shadow_line`, разбирает
#: `parse_shadow_line` — одно место на запись и чтение.
SHADOW_PREFIX = "gate-shadow:"

#: Сколько секунд прогон тени может думать, прежде чем пропуск станет «hung», а не
#: «busy»: окно ответа ⚡ (слот подсказки ждёт 45 с). Дольше — вердикт живому гейту
#: всё равно не успел бы, а замер должен видеть, что решатель встал (круг 2 по #651, DS I2).
HUNG_S = 45.0

#: Файлы обученной головы. Нет хоть одного — головы нет.
HEAD_FILES = ("model.onnx", "tokenizer.json", "labels.json")


def shadow_enabled(cfg: dict) -> bool:
    """Включена ли тень. Только буквальное `true`: молчание конфига — «нет»."""
    return (cfg.get("sufler") or {}).get(SHADOW_KEY, False) is True


@dataclasses.dataclass(frozen=True)
class Verdict:
    """Решение гейта по одной реплике."""

    label: str
    confidence: float
    probs: Mapping[str, float]
    backend: str
    ms: float


@dataclasses.dataclass(frozen=True)
class Decider:
    """Решатель как значение: имя, функция вероятностей, причина отказа.

    `probs(text)` отдаёт распределение по `LABELS`. Судить нечем — бросает
    исключение, а не отдаёт равные доли: 0.5/0.5 неотличимо от честного
    «не уверен», и тень записала бы шум под видом вердикта.
    """

    name: str
    probs: Callable[[str], Mapping[str, float]] = dataclasses.field(repr=False)
    refused: str = ""


def normalize(scores: Mapping[str, float]) -> dict[str, float]:
    """Оценки гипотез → распределение. Все нули — судить не по чему.

    Отрицательная или нечисловая оценка — ошибка проводки, а не «мало
    уверенности»: вероятность следования лежит в [0, 1].
    """
    for label, s in scores.items():
        if not isinstance(s, (int, float)) or isinstance(s, bool) or not math.isfinite(s) or s < 0:
            raise ValueError(f"оценка «{label}» — не вероятность: {s!r}")
    total = sum(scores.values())
    if total <= 0:
        raise ValueError("ни одна гипотеза не следует из реплики — судить не по чему")
    return {label: s / total for label, s in scores.items()}


def zero_shot_probs(text: str, entail: Callable[[str, str], float],
                    hypotheses: Mapping[str, str] = HYPOTHESES) -> dict[str, float]:
    """Zero-shot через NLI: P(следует гипотеза метки | реплика), нормировано."""
    missing = set(LABELS) - set(hypotheses)
    if missing:
        raise ValueError(f"нет гипотез для меток: {sorted(missing)}")
    return normalize({label: entail(text, hypotheses[label]) for label in LABELS})


def calibrated(logits: list[float], temperature: float) -> list[float]:
    """Softmax с температурой: так калибруют уверенность обученной головы.

    Температуру подбирают на отложенной разметке (ECE до/после — в
    `gate_bench.py eval`); T > 1 смягчает самоуверенную голову.
    """
    if not (math.isfinite(temperature) and temperature > 0):
        raise ValueError(f"температура — конечное число > 0, а не {temperature!r}")
    scaled = [x / temperature for x in logits]
    top = max(scaled)
    exps = [math.exp(x - top) for x in scaled]
    total = sum(exps)
    return [e / total for e in exps]


def decide(decider: Decider, text: str,
           clock: Callable[[], float] = time.perf_counter) -> Verdict:
    """Один вердикт с замером задержки."""
    started = clock()
    probs = dict(decider.probs(text))
    ms = (clock() - started) * 1000.0
    if set(probs) != set(LABELS):
        raise ValueError(f"решатель {decider.name} вернул метки {sorted(probs)}, ждали {list(LABELS)}")
    label = max(LABELS, key=lambda k: probs[k])
    return Verdict(label, probs[label], probs, decider.name, ms)


def cascade(verdict: Verdict | None, accept_at: float) -> str:
    """Каскад: метка, если гейт уверен не меньше порога, иначе «escalate».

    Порог — доля из (0.5, 1]: при двух метках уверенность победителя не ниже
    0.5, и порог 0.5 означал бы «решаю всегда» — это уже не каскад.
    `escalate` — отдать реплику модели, как сегодня.
    """
    if not (isinstance(accept_at, (int, float)) and not isinstance(accept_at, bool)
            and 0.5 < accept_at <= 1.0):
        raise ValueError(f"порог каскада — число из (0.5, 1], а не {accept_at!r}")
    if verdict is None or verdict.confidence < accept_at:
        return "escalate"
    return verdict.label


def outcome_of(answer: str, refusal: bool) -> str:
    """Исход ⚡ для тени: отказ модели, ответ или ничего."""
    if refusal:
        return "refusal"
    return "answered" if (answer or "").strip() else "failed"


# --- обученная голова -------------------------------------------------------

def read_head_meta(raw: str) -> tuple[tuple[str, ...], float, int]:
    """`labels.json` → (порядок меток в логитах, температура, max_len).

    Порядок меток — порядок выходов головы; перепутать его значит инвертировать
    гейт молча, поэтому метки обязаны быть ровно `LABELS` в любом порядке.
    """
    meta = json.loads(raw)
    labels = tuple(meta.get("labels") or ())
    if len(labels) != len(LABELS) or set(labels) != set(LABELS):
        raise ValueError(f"labels.json: метки {list(labels)}, ждали перестановку {list(LABELS)}")
    temperature = meta.get("temperature", 1.0)
    if not (isinstance(temperature, (int, float)) and not isinstance(temperature, bool)
            and math.isfinite(temperature) and temperature > 0):
        raise ValueError(f"labels.json: температура — число > 0, а не {temperature!r}")
    max_len = meta.get("max_len", 256)
    if not (isinstance(max_len, int) and not isinstance(max_len, bool) and max_len >= 8):
        raise ValueError(f"labels.json: max_len — целое ≥ 8, а не {max_len!r}")
    return labels, float(temperature), max_len


class OnnxHead:
    """Обученная голова в ONNX: реплика → логиты меток → калиброванные доли.

    Сессия и токенизатор приходят параметрами: загрузчик собирает настоящие
    (onnxruntime + tokenizers, без torch — как `nli.py`), тест — подделки.
    """

    def __init__(self, session, tokenizer, labels: tuple[str, ...],
                 temperature: float = 1.0) -> None:
        self._session = session
        self._tokenizer = tokenizer
        self._labels = labels
        self._temperature = temperature
        self._inputs = {i.name for i in session.get_inputs()}

    def __call__(self, text: str) -> dict[str, float]:
        import numpy as np

        enc = self._tokenizer.encode(text)
        feed = {
            "input_ids": np.array([enc.ids], dtype=np.int64),
            "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
            "token_type_ids": np.array([enc.type_ids], dtype=np.int64),
        }
        # экспорты расходятся во входах (ModernBERT без token_type_ids) —
        # подаём ровно то, что сессия объявила
        feed = {k: v for k, v in feed.items() if k in self._inputs}
        logits = [float(x) for x in self._session.run(None, feed)[0][0]]
        if len(logits) != len(self._labels):
            raise ValueError(f"голова отдала {len(logits)} логитов на {len(self._labels)} меток")
        return dict(zip(self._labels, calibrated(logits, self._temperature)))


def default_head_dir() -> pathlib.Path:
    """Где лежит голова — по корню данных НА ВЫЗОВЕ (урок №329, см. `nli._dir`)."""
    return resolve_root(__file__) / MODELS_DIR / "decision" / "question_gate"


def load_head(directory: pathlib.Path) -> OnnxHead:
    """Собрать голову с диска. Любая поломка — исключение с причиной."""
    import onnxruntime as ort
    from tokenizers import Tokenizer

    labels, temperature, max_len = read_head_meta(
        (directory / "labels.json").read_text(encoding="utf-8"))
    tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=max_len)
    opts = ort.SessionOptions()
    # два потока, как у NLI: рядом живёт STT, и отбирать у него ядра ради
    # миллисекунд гейта нельзя
    opts.intra_op_num_threads = 2
    session = ort.InferenceSession(str(directory / "model.onnx"), opts,
                                   providers=["CPUExecutionProvider"])
    return OnnxHead(session, tokenizer, labels, temperature)


class _Lazy:
    """Загрузка при первом вызове, один раз, под замком.

    Провал загрузки запоминается: каждая реплика иначе заново читала бы
    гигабайт с диска, чтобы упасть тем же исключением.
    """

    def __init__(self, load: Callable[[], Callable[[str], Mapping[str, float]]]) -> None:
        self._load = load
        self._lock = threading.Lock()
        self._fn: Callable[[str], Mapping[str, float]] | None = None
        self._failed: BaseException | None = None

    def __call__(self, text: str) -> Mapping[str, float]:
        with self._lock:
            if self._fn is None and self._failed is None:
                try:
                    self._fn = self._load()
                except Exception as e:  # noqa: BLE001 — любая поломка сборки = «решателя нет»; причина уезжает в RuntimeError ниже
                    self._failed = e
            if self._failed is not None:
                raise RuntimeError(f"решатель не поднялся: {type(self._failed).__name__}") from self._failed
            fn = self._fn
        return fn(text)


def _refuse(_text: str) -> Mapping[str, float]:
    raise RuntimeError("решателя нет")


def decider(head_dir: pathlib.Path | None = None, judge: Callable[[], object] | None = None,
            hypotheses: Mapping[str, str] = HYPOTHESES) -> Decider:
    """Лучший доступный решатель: обученная голова, иначе NLI zero-shot.

    Дешёвая фаза — здесь: наличие файлов и пакетов. Дорогая (подъём сессии)
    — при первом вызове `probs`, уже в потоке тени, а не на старте демона.
    """
    d = head_dir if head_dir is not None else default_head_dir()
    if all((d / f).exists() for f in HEAD_FILES):
        return Decider(f"head:{d.name}", _Lazy(lambda: load_head(d)))
    j = (judge or nli.judge)()
    if not j.refused:
        return Decider("nli-zero-shot",
                       lambda text: zero_shot_probs(text, j.entail, hypotheses))
    return Decider("none", _refuse, refused=(
        f"нет ни обученной головы в {d}, ни NLI-модели — гейт в тени выключен"))


# --- тень ---------------------------------------------------------------------

def shadow_line(verdict: Verdict | None, outcome: str, reason: str = "") -> str:
    """Строка тени для err-лога. Ни слова из разговора и ничего, выведенного из
    него: метка, уверенность, задержка и исход ⚡.

    Хеша вопроса в строке нет: 40 бит SHA-256 без соли от короткой нормализованной
    реплики подбираются перебором по любому списку кандидатов, а склейки с
    `_hints.md`, ради которой он был, в коде нет (выходной круг 1 по #651, DS I1).
    Понадобится склейка — ключом станет место в `_hints.md`, а не отпечаток текста.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"исход тени — один из {OUTCOMES}, а не {outcome!r}")
    if verdict is None:
        return f"{SHADOW_PREFIX} verdict=none reason={reason or 'unknown'} outcome={outcome}"
    return (f"{SHADOW_PREFIX} backend={verdict.backend} label={verdict.label} "
            f"p={verdict.confidence:.3f} ms={verdict.ms:.0f} outcome={outcome}")


def parse_shadow_line(line: str) -> dict | None:
    """Разбор строки тени. Чужая строка или битая — None, а не исключение:
    err-лог общий, и в нём живут строки всех контуров. Поле `q` строк прежнего
    формата читается как лишнее и ни на что не влияет."""
    at = line.find(SHADOW_PREFIX)
    if at < 0:
        return None
    fields: dict[str, object] = {}
    for token in line[at + len(SHADOW_PREFIX):].split():
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value
    if fields.get("outcome") not in OUTCOMES:
        return None
    if fields.get("verdict") == "none":
        return fields
    if fields.get("label") not in LABELS:
        return None
    try:
        fields["p"] = float(fields["p"])
        fields["ms"] = float(fields["ms"])
    except (KeyError, ValueError):
        return None
    return fields


class ShadowRun:
    """Вердикт в тени одного ⚡: считается параллельно генерации, ничего не решает.

    `finish(outcome)` не ждёт решателя: исход запоминается, и строку пишет
    тот, кто пришёл вторым, — поток решателя или сам `finish`. Так тень не
    добавляет ⚡ ни миллисекунды и не теряет запись, если первая загрузка
    модели дольше ответа. Строка пишется ровно один раз.

    Прогон без потока (`skip`) — вердикта не будет по названной причине
    (решатель ещё занят прошлым вопросом, поток не стартовал): `finish` пишет
    строку `verdict=none reason=…`, и замер видит пропуск, а не тишину.
    """

    def __init__(self, question: str, dec: Decider, log: Callable[[str], None],
                 clock: Callable[[], float] = time.perf_counter) -> None:
        self._question = question
        self._decider = dec
        self._log = log
        self._clock = clock
        self._lock = threading.Lock()
        self._verdict: Verdict | None = None
        self._reason = ""
        self._done = False
        self._outcome: str | None = None
        self._written = False
        self._thread = threading.Thread(target=self._run, name="gate-shadow", daemon=True)

    def start(self) -> None:
        """Поток решателя. Может бросить (`RuntimeError` при исчерпании потоков
        процесса) — `Shadow.start` превращает это в пропуск с причиной."""
        self._thread.start()

    def skip(self, reason: str) -> None:
        """Вердикта не будет: решатель не звался, причина уйдёт в строку."""
        with self._lock:
            self._reason, self._done = reason, True

    @property
    def decided(self) -> bool:
        """Решатель отдал вердикт или отказ (либо прогон пропущен)."""
        with self._lock:
            return self._done

    def _run(self) -> None:
        verdict, reason = None, "error:BaseException"
        try:
            verdict = decide(self._decider, self._question, self._clock)
            reason = ""
        except Exception as e:  # noqa: BLE001 — тень не смеет ронять поток; класс сбоя уходит в строку лога
            reason = f"error:{type(e).__name__}"
        finally:
            # и на BaseException прогон отмечается решённым: иначе «в полёте» навсегда (DS I2)
            with self._lock:
                self._verdict, self._reason, self._done = verdict, reason, True
                ready = self._outcome is not None
            if ready:
                self._write()

    def finish(self, outcome: str) -> None:
        """Исход ⚡ известен. Не бросает: тень не смеет ронять поток ⚡ (выходной
        круг 1 по #651, DS C1). Исход не из `OUTCOMES` — «failed»: что ответила
        модель, тени неизвестно; `outcome_of` отдаёт только законные исходы, и
        это держат её тесты."""
        if outcome not in OUTCOMES:
            outcome = "failed"
        with self._lock:
            if self._outcome is not None:
                return
            self._outcome = outcome
            ready = self._done
        if ready:
            self._write()

    def _write(self) -> None:
        with self._lock:
            if self._written:
                return
            self._written = True
            line = shadow_line(self._verdict, self._outcome or "failed", self._reason)
        try:
            self._log(line)
        except Exception:  # noqa: BLE001 — сток тени (stderr) закрыт или сломан: тень — наблюдение, а не контур, ⚡ идёт дальше
            pass

    def join(self, timeout: float | None = None) -> None:
        """Дождаться решателя (тестам и скриптам; демон не ждёт)."""
        if self._thread.ident is not None:
            self._thread.join(timeout)


class _Idle:
    """Прогон, которого нет: тень выключена или решателя нет. Тот же интерфейс,
    что у `ShadowRun`, и ничего не пишет — поэтому цикл ⚡ зовёт `finish` без
    условия, и в демоне нет ветки, до которой не дотягивается ни один тест.

    Объект один на процесс, и состояния у него нет по построению: `__slots__`
    пуст, записать атрибут нельзя — общий объект не протечёт от вопроса к
    вопросу (выходной круг 3 по #651, DS M1)."""

    __slots__ = ()
    decided = True

    def finish(self, outcome: str) -> None:
        return None

    def join(self, timeout: float | None = None) -> None:
        return None


IDLE = _Idle()


def _stderr_line(line: str) -> None:
    """Строка в stderr одним `write`: `print` пишет строку и перевод двумя, и
    строки тени из потока решателя и из потока ⚡ склеивались бы в одну — пропуск
    `busy`/`hung` молча пропал бы из замера (выходной круг 3 по #651, DS M2;
    то же правило, что у `once.say`). `sys.stderr` берётся в момент вызова:
    демон подменяет поток файлом err-лога после импорта."""
    stream = sys.stderr
    stream.write(line + "\n")
    stream.flush()


def shadow_for(cfg: dict, instant_on: bool,
               log: Callable[[str], None] = _stderr_line) -> "Shadow":
    """Тень на встречу по конфигу — вся проводка демона одним вызовом.

    Выключено ⚡ или тень — `Shadow` без решателя, его `start` отдаёт `IDLE`.
    Включено — строка о решателе на старте (какой поднялся или почему нет) и
    тот же сток для строк тени. Прежде эти условия жили в демоне, и мутации CI
    их не видели ни одним тестом (выходной круг 2 по #651, CI).
    """
    dec = decider() if instant_on and shadow_enabled(cfg) else None
    shadow = Shadow(dec, log)
    if dec is not None:
        shadow.say(f"гейт в тени: {dec.refused or dec.name}")
    return shadow


class Shadow:
    """Тень ⚡ на встречу: решатель, сток и не больше одного прогона в полёте.

    Шов тотальный: `start` и `ShadowRun.finish` не бросают ничего, поэтому в
    цикле ⚡ нет ни одного `try` вокруг тени (выходной круг 1 по #651, DS C1).
    Прогон один в полёте: пока решатель думает над прошлым вопросом, новый вопрос
    получает строку `reason=busy`, а не ещё один поток. Зависший решатель тогда
    виден в замере ростом пропусков, а не копит потоки молча (DS I3).
    """

    def __init__(self, dec: Decider | None, log: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._decider = dec
        self._log = log
        self._clock = clock
        self._lock = threading.Lock()
        self._inflight: ShadowRun | None = None
        self._inflight_at = 0.0

    @property
    def active(self) -> bool:
        """Есть чем судить: решатель назван и не отказал."""
        return self._decider is not None and not self._decider.refused

    def say(self, line: str) -> None:
        """Строка в сток тени; сток сломан — тень молчит, демон идёт дальше."""
        try:
            self._log(line)
        except Exception:  # noqa: BLE001 — сток тени (stderr) закрыт или сломан: тень — наблюдение, а не контур
            pass

    def start(self, question: str) -> "ShadowRun | _Idle":
        """Тень на один вопрос — или `IDLE`, если судить нечем.

        Пустой вопрос при живом решателе — не тишина, а пропуск с причиной
        `no-question`: ⚡ отвечает и на хвост без вопроса («⚡ мгновенный
        ответ»), и замеру нужен знаменатель — сколько ответов прошло мимо
        гейта (выходной круг 3 по #651, DS M3)."""
        if not self.active:
            return IDLE
        if not (question or "").strip():
            run = ShadowRun("", self._decider, self._log)
            run.skip("no-question")
            return run
        with self._lock:
            run = ShadowRun(question, self._decider, self._log)
            if self._inflight is not None and not self._inflight.decided:
                # возраст прогона в полёте — в причине: «медленно» и «встал» различимы
                # по строке, а не глазами по счёту причин (круг 2 по #651, DS I2)
                age = self._clock() - self._inflight_at
                run.skip("busy" if age < HUNG_S else f"hung:{age:.0f}s")
                return run
            try:
                run.start()
            except Exception as e:  # noqa: BLE001 — отказ старта потока (лимит потоков, память) не смеет ронять ⚡: причина уходит в строку тени
                run.skip(f"start:{type(e).__name__}")
                return run
            self._inflight, self._inflight_at = run, self._clock()
        return run
