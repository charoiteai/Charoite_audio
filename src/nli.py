"""NLI-слой: локальная проверка «следует ли B из A» без torch.

mDeBERTa-v3-base-xnli (ONNX, ~1.1 ГБ) + onnxruntime + tokenizers — никаких
transformers/torch: демону нужен один инференс, а не фреймворк на гигабайты.

Слой ОПЦИОНАЛЬНЫЙ: без модели демон работает как раньше, просто без
смыслового дедупа тезисов. Чтобы включить, положите в models/nli/ три файла
модели MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7 в
ONNX-экспорте: model.onnx, tokenizer.json, config.json (экспорт — optimum:
`optimum-cli export onnx --model MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7 models/nli/`).

Зачем демону NLI. Эмбеддинги (BGE-M3) меряют «о том же ли это», NLI меряет
«утверждает ли это то же самое» — разница решающая для дедупа тезисов:
«бюджет согласован» и «бюджет не согласован» по эмбеддингам почти близнецы,
по NLI — противоречие. Дубль = двустороннее следование (A⇒B и B⇒A).

Ленивая инициализация: модель грузится при первом вызове (~2-3 с), до того
демон стартует как раньше. Нет модели/пакетов — is_available() честно False,
вызывающий код живёт без дедупа, а не падает.
"""

from __future__ import annotations

import pathlib
import threading

from charoite_paths import MODELS_DIR, resolve_root
from model_seam import Judge, SeamTransportError

# Модели — ДАННЫЕ, а не поставка: `/models/` стоит в .gitignore, в подписанный
# бандл каталог не попадает, и качает их скрипт моделей в корень данных. Так же
# их ищет диаризация (`diarize.SEG_MODEL`). Раньше здесь стояла цепочка от
# положения файла, то есть корень КОДА: в репозитории оба корня совпадают и
# дефект был невидим, а во вложенной установке модель лежала в одном месте, а
# искалась в другом — `is_available()` честно отвечал False, и смысловой дедуп
# тезисов молча выключался навсегда (обе головы входного круга №321).
def _dir() -> pathlib.Path:
    """Где лежит модель — по корню данных НА ВЫЗОВЕ.

    Снимок на импорте брался раньше, чем точка входа называла корень: модель
    искалась в выведенном из положения файла каталоге, `is_available()` честно
    отвечал False, и смысловой дедуп тезисов молча выключался (№329, тот же
    класс, что закрывали входным кругом №321).
    """
    return resolve_root(__file__) / MODELS_DIR / "nli"

_lock = threading.Lock()
_session = None
_tokenizer = None
_failed = False


def is_available() -> bool:
    """Модель на диске и пакеты на месте (без загрузки самой модели)."""
    if _failed:
        return False
    if not (_dir() / "model.onnx").exists() or not (_dir() / "tokenizer.json").exists():
        return False
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        return False
    return True


def _load():
    global _session, _tokenizer, _failed
    with _lock:
        if _session is not None or _failed:
            return
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            _tokenizer = Tokenizer.from_file(str(_dir() / "tokenizer.json"))
            # один поток: NLI зовётся из фоновых потоков демона, где рядом
            # живёт STT — не отбирать у него ядра ради миллисекунд
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            _session = ort.InferenceSession(
                str(_dir() / "model.onnx"), opts, providers=["CPUExecutionProvider"])
        except Exception:
            _failed = True


def ready() -> bool:
    """Модель на месте И сессия поднимается: то, что нужно знать до суда.

    `is_available()` смотрит только на файлы; `entail_prob` при не собравшейся
    сессии молча отдаёт 0.0 — вызывающему не отличить «не дубль» от «судья не
    пришёл». Здесь — честный ответ.
    """
    if not is_available():
        return False
    _load()
    return _session is not None


def entail_prob(premise: str, hypothesis: str) -> float:
    """P(entailment): насколько из premise СЛЕДУЕТ hypothesis. 0.0 если слой недоступен."""
    if not is_available():
        return 0.0
    _load()
    if _session is None:
        return 0.0
    import numpy as np

    enc = _tokenizer.encode(premise, hypothesis)
    ids = enc.ids[:512]
    mask = enc.attention_mask[:512]
    feed = {
        "input_ids": np.array([ids], dtype=np.int64),
        "attention_mask": np.array([mask], dtype=np.int64),
    }
    # часть экспортов DeBERTa требует token_type_ids — подаём, только если вход объявлен
    names = {i.name for i in _session.get_inputs()}
    if "token_type_ids" in names:
        feed["token_type_ids"] = np.array([enc.type_ids[:512]], dtype=np.int64)
    logits = _session.run(None, feed)[0][0]
    # softmax над [entailment, neutral, contradiction] (порядок из config.json)
    e = np.exp(logits - logits.max())
    return float((e / e.sum())[0])


def is_duplicate(a: str, b: str, threshold: float = 0.75) -> bool:
    """Дубль по смыслу: A и B взаимно следуют друг из друга.

    Однонаправленного следования мало: «бюджет согласован на 2 млн» ⇒
    «бюджет согласован», но это уточнение, а не дубль — терять его нельзя.
    """
    if not a or not b:
        return False
    return entail_prob(a, b) >= threshold and entail_prob(b, a) >= threshold


#: Пара, на которой проверяется живость поднятой сессии: из утверждения следует
#: оно само. Битая или недогруженная модель отвечает на неё мусором — и это
#: единственный способ отличить «сессия собралась» от «сессия судит».
_CANARY = ("Интеграция платёжного шлюза идёт по плану.",
           "Интеграция платёжного шлюза идёт по плану.")


def judge() -> Judge:
    """Судья как значение: дешёвый отказ при сборке, дорогая готовность по требованию.

    Дешёвая фаза считается здесь и сейчас — ровно то, что делает
    `is_available()`. Потребителю она достаётся строкой `refused`, и на
    установке без модели он выходит, не тронув ни ядер, ни эмбеддера.

    `ready()` поднимает сессию и спрашивает её о паре, ответ на которую известен
    заранее. Сессия, которая «собралась», но отвечает нулями, не должна
    проходить за готовую: именно на этом стояли два дефекта подряд — прогон
    объявлялся состоявшимся, суд молча не находил ничего, а отметка инкремента
    уезжала вперёд, и свежие ядра выпадали из фокуса навсегда.

    `entail` отказывает исключением, а не нулём. Ноль — это «не следует», и
    подменять им «судить нечем» значит терять пару молча: вызывающий
    возвращает в фокус следующего прогона те пары, на которых судья отказал,
    и не возвращает те, которые честно разошлись.
    """
    why = "" if is_available() else (
        f"NLI-модель не найдена в {_dir()} или нет onnxruntime/tokenizers — "
        "смысловой дедуп ядер выключен")

    def _ready() -> bool:
        if not ready():
            return False
        try:
            return entail_prob(*_CANARY) > 0.5
        except Exception:      # noqa: BLE001 — сессия есть, но судить не может
            return False

    def _entail(premise: str, hypothesis: str) -> float:
        if not is_available():
            raise SeamTransportError("судья исчез посреди прогона: модель или пакеты пропали")
        _load()
        if _session is None:
            raise SeamTransportError("судья не поднялся: ONNX-сессия не собралась")
        return entail_prob(premise, hypothesis)

    return Judge(_ready, _entail, why)
