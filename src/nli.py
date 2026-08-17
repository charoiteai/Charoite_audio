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

_DIR = pathlib.Path(__file__).resolve().parent.parent / "models" / "nli"

_lock = threading.Lock()
_session = None
_tokenizer = None
_failed = False


def is_available() -> bool:
    """Модель на диске и пакеты на месте (без загрузки самой модели)."""
    if _failed:
        return False
    if not (_DIR / "model.onnx").exists() or not (_DIR / "tokenizer.json").exists():
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

            _tokenizer = Tokenizer.from_file(str(_DIR / "tokenizer.json"))
            # один поток: NLI зовётся из фоновых потоков демона, где рядом
            # живёт STT — не отбирать у него ядра ради миллисекунд
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            _session = ort.InferenceSession(
                str(_DIR / "model.onnx"), opts, providers=["CPUExecutionProvider"])
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
