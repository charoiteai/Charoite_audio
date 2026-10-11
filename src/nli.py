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

import json
import pathlib
import threading

from charoite_paths import MODELS_DIR, resolve_root
from charoite_graph.model_seam import Judge, SeamTransportError

# Модели — ДАННЫЕ, а не поставка: `/models/` стоит в .gitignore, в подписанный
# бандл каталог не попадает, и качает их скрипт моделей в корень данных. Так же
# их ищет диаризация (`diarize._seg_model()`). Раньше здесь стояла цепочка от
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
_why = ""             # причина, по которой не собралась сессия (_failed)
_entail_index = None
_cfg_cache = None     # ((путь, mtime_ns, размер), причина отказа или "")


def _label_index(path: pathlib.Path) -> int:
    """Не угадывать смысл логита: нужен однозначный контракт трёх классов."""
    meta = json.loads(path.read_text(encoding="utf-8"))
    labels = meta.get("id2label") if isinstance(meta, dict) else None
    if not isinstance(labels, dict) or set(labels) != {"0", "1", "2"}:
        raise ValueError("нужен id2label с индексами 0, 1, 2")
    names = [labels[str(i)] for i in range(3)]
    if any(not isinstance(name, str) for name in names):
        raise ValueError("метки id2label должны быть строками")
    names = [name.strip().lower() for name in names]
    if set(names) != {"entailment", "neutral", "contradiction"}:
        raise ValueError("метки id2label не из entailment/neutral/contradiction: "
                         f"{[labels[str(i)] for i in range(3)]}")
    return names.index("entailment")


def _problem() -> str:
    """Дешёвая проверка файлов модели; пустая строка — файлы годны.

    config.json разбирается здесь, а не только при загрузке: иначе неверные
    метки всплывали бы лишь после чтения ядер и пробуждения эмбеддера, и с
    чужой причиной. Разбор кэшируется по mtime — is_available() зовут на
    каждую пару тезисов.
    """
    global _cfg_cache
    d = _dir()
    for name in ("model.onnx", "tokenizer.json"):
        if not (d / name).exists():
            return f"нет файла {name} в {d}"
    cfg = d / "config.json"
    try:
        st = cfg.stat()
    except OSError:
        return f"нет config.json в {d}: порядок классов не проверить (см. docs/MODELS.md)"
    key = (str(cfg), st.st_mtime_ns, st.st_size)
    cached = _cfg_cache
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        _label_index(cfg)
        why = ""
    except (OSError, ValueError) as e:  # ValueError покрывает и битый JSON
        why = f"{cfg}: {e} (см. docs/MODELS.md)"
    _cfg_cache = (key, why)
    return why


def _packages() -> str:
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        return "нет onnxruntime/tokenizers"
    return ""


def _refusal() -> str:
    """Почему судить нечем; пустая строка — файлы и пакеты на месте."""
    if _failed:
        return _why
    return _problem() or _packages()


def refusal() -> str:
    """Публичная причина отказа судьи (для логов потребителей); "" — судить есть чем."""
    return _refusal()


def is_available() -> bool:
    """Модель (model.onnx, tokenizer.json, config.json с годными метками) на
    диске и пакеты на месте — без загрузки самой модели."""
    return not _refusal()


def _load():
    global _session, _tokenizer, _failed, _why, _entail_index
    with _lock:
        if _session is not None or _failed:
            return
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            _entail_index = _label_index(_dir() / "config.json")
            _tokenizer = Tokenizer.from_file(str(_dir() / "tokenizer.json"))
            # один поток: NLI зовётся из фоновых потоков демона, где рядом
            # живёт STT — не отбирать у него ядра ради миллисекунд
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            _session = ort.InferenceSession(
                str(_dir() / "model.onnx"), opts, providers=["CPUExecutionProvider"])
        # любая поломка сборки сессии = «судья не пришёл»: ready() и _entail
        # говорят это честно, с причиной
        except Exception as e:  # noqa: BLE001
            _why = f"ONNX-сессия не собралась: {type(e).__name__}: {e}; перезапустите демон"
            _failed = True
            print(f"NLI: {_why}", flush=True)


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
    """P(entailment): насколько из premise СЛЕДУЕТ hypothesis.

    0.0 — если нет файлов/пакетов или сессия не собралась. Выход модели не из
    трёх конечных логитов — SeamTransportError.
    """
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
    if logits.shape != (3,) or not np.isfinite(logits).all():
        raise SeamTransportError("NLI: выход не соответствует контракту трёх классов")
    # softmax: порядок классов проверен по config.json при загрузке.
    e = np.exp(logits - logits.max())
    return float((e / e.sum())[_entail_index])


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
    why = _refusal()
    if why:
        why = f"{why} — смысловой дедуп ядер выключен"

    def _ready() -> bool:
        if not ready():
            return False
        try:
            return entail_prob(*_CANARY) > 0.5
        except Exception:      # noqa: BLE001 — сессия есть, но судить не может
            return False

    def _entail(premise: str, hypothesis: str) -> float:
        why = _refusal()
        if not why:
            _load()
            why = "" if _session is not None else (_refusal() or "ONNX-сессия не собралась")
        if why:
            raise SeamTransportError(f"NLI-судья отказал: {why}")
        return entail_prob(premise, hypothesis)

    return Judge(_ready, _entail, why)
