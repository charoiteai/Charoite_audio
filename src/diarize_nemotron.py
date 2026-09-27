"""Nemotron 3 Diarization — экспериментальный движок диаризации (NVIDIA, 23.09.2026).

Зачем. Живой трекер и проход после встречи устроены одинаково: сегментация
режет речь на куски, ERes2Net даёт каждому куску эмбеддинг, косинус решает,
чей это голос. Один кусок — один голос. Когда двое говорят разом (перебили,
заспорили), такой конвейер видит одного из них в лучшем случае, а в худшем —
смешанный эмбеддинг, который не похож ни на кого.

Nemotron 3 Diarization — end-to-end Sortformer на ~100M параметров: на каждый
10-мс кадр он выдаёт вероятность активности каждого из восьми голосов, и два
голоса в одном кадре — штатный ответ, а не сбой. Модель стриминговая: память
о голосах (кэш AOSC + FIFO) живёт в состоянии потока, задержка входного буфера
настраивается от 0.32 с до 30.4 с (пресеты NVIDIA, `PRESETS`).

Статус — ЭКСПЕРИМЕНТ. Ни демон, ни пересборка стенограммы этот модуль не
зовут. Его зовёт бенч (`scripts/diar_bench.py --engine nemotron…`): сначала
число на своих записях, потом решение о встраивании.

Только Apple Silicon. Инференс — MLX-порт из пакета mlx-audio
(`mlx-community/Nemotron-3-Diarization`). В зависимости продукта пакет не
входит и ставится руками (`INSTALL_RECIPE`); импорт mlx ленивый, поэтому
модуль импортируется на любой машине, а чистые функции проверяются в CI.

Сеть. Загрузчик принимает только существующий каталог с config.json и весами
и передаёт в mlx-audio `pathlib.Path`: для Path библиотека не обращается к
хабу (проверено по коду mlx-audio 0.5.6, `base_load_model`). Строка вида
«org/name» здесь — отказ с рецептом, а не скачивание: загрузка весов — разовое
действие человека, как `scripts/get_models.py`.

Голос. Кэш голосов живёт в состоянии потока, в RAM, и умирает вместе с
объектом; наш код не пишет на диск ничего производного от голоса — это держат
статический сторож (`tests/test_no_voice_biometrics.py`) и прогон обёртки с
подменённой моделью. Что пишет сама mlx-audio, по коду не проверить: это
прогон по звуку с весами на Mac (№443).

Лицензия весов — NVIDIA OpenMDW 1.1, не Apache-2.0: веса в репозиторий не
кладутся, человек скачивает их сам и принимает условия.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import pathlib
from typing import Any, Iterable, Protocol

import numpy as np

SAMPLE_RATE = 16000

#: `model_type` в config.json чекпойнта mlx-audio (convert.py пишет asdict(config)).
MODEL_TYPE = "nemotron_diarization"

#: Пресеты задержки NVIDIA (входной буфер, без вычислений и окна STFT):
#: offline — 30.4 с, low — 1.04 с, very_low — 0.64 с, ultra_low — 0.32 с.
PRESETS = ("offline", "low", "very_low", "ultra_low")

HF_REPO = "mlx-community/Nemotron-3-Diarization"
#: Версия — та, по коду которой проверен стык (`load`, `set_streaming_config`, `feed`):
#: дрейф API иначе выяснился бы только на ручном прогоне (круг 1 по #648, DS M2).
#: `availability` сверяет её с установленной (круг 2, DS I2).
MLX_AUDIO_VERSION = "0.5.6"
INSTALL_RECIPE = f'.venv/bin/pip install "mlx-audio=={MLX_AUDIO_VERSION}"   # только macOS на Apple Silicon'

#: Нижняя граница размера файла весов. Полная модель — сотни мегабайт, 8-битная
#: — около сотни. Меньше — обрыв закачки, HTML-страница или указатель git-lfs
#: (~130 байт: клон репозитория без LFS выглядит как модель, но ею не является).
MIN_WEIGHTS_BYTES = 20 * 1024 * 1024


class ModelUnavailable(RuntimeError):
    """Модель нельзя поднять; текст — что не так и что сделать."""


class _Segment(Protocol):
    start: float
    end: float
    speaker: int


def model_dir(root: pathlib.Path) -> pathlib.Path:
    """Где ждём веса: рядом с моделями живой диаризации, в корне данных."""
    return root / "models" / "diar" / "nemotron"


def fetch_recipe(target: pathlib.Path) -> str:
    """Команда разовой загрузки весов — печатается, но не выполняется."""
    return f".venv/bin/hf download {HF_REPO} --local-dir {target}"


def check_model_dir(path: pathlib.Path) -> str | None:
    """None — каталог годится; иначе строка «что не так и что сделать».

    Проверка до импорта mlx: сообщение нужно человеку и там, где mlx нет.
    """
    path = pathlib.Path(path)
    recipe = fetch_recipe(path)
    if not path.is_dir():
        return f"нет каталога модели {path} — скачать: {recipe}"
    config = path / "config.json"
    if not config.is_file():
        return f"в {path} нет config.json — скачать заново: {recipe}"
    try:
        model_type = json.loads(config.read_text(encoding="utf-8")).get("model_type")
    except (OSError, ValueError, AttributeError) as e:
        # OSError — права, битый том: тоже рецепт, а не трассировка (DS I4)
        return f"{config} не читается как JSON-объект ({type(e).__name__}: {e}) — скачать заново: {recipe}"
    if model_type != MODEL_TYPE:
        return (f"в {path} не Nemotron Diarization: model_type={model_type!r}, "
                f"ждали {MODEL_TYPE!r} — скачать: {recipe}")
    weights = sorted(path.glob("*.safetensors"))
    if not weights:
        return f"в {path} нет весов (*.safetensors) — скачать: {recipe}"
    try:
        small = [w.name for w in weights if w.stat().st_size < MIN_WEIGHTS_BYTES]
    except OSError as e:
        # тот же битый том или права, что у config.json выше, — рецепт, а не трассировка (DS M4)
        return f"веса в {path} не читаются ({type(e).__name__}: {e}) — скачать заново: {recipe}"
    if small:
        return (f"веса в {path} подозрительно малы ({', '.join(small)}): обрыв "
                f"закачки или указатель git-lfs — скачать заново: {recipe}")
    return None


def availability(path: pathlib.Path) -> str | None:
    """Всё, чего не хватает для прогона, одним ответом — до долгой работы.

    None — пакет найден и каталог весов годен. Иначе — каждая проблема своей
    строкой с рецептом: человек ставит всё за один заход, а не по одной ошибке.
    """
    problems = []
    if importlib.util.find_spec("mlx_audio") is None:
        problems.append(f"нет пакета mlx-audio — {INSTALL_RECIPE}")
    else:
        try:
            installed = importlib.metadata.version("mlx-audio")
        except importlib.metadata.PackageNotFoundError:
            installed = None
        if installed != MLX_AUDIO_VERSION:
            # стык проверен на одной версии: чужая — отказ до прогона с рецептом, а не
            # «скачайте веса заново» после (круг 2 по #648, DS I2)
            problems.append(f"mlx-audio {installed or '?'}, стык проверен на {MLX_AUDIO_VERSION} — "
                            f"{INSTALL_RECIPE}")
    problem = check_model_dir(path)
    if problem:
        problems.append(problem)
    return "\n".join(problems) or None


def load_model(path: pathlib.Path, preset: str = "offline") -> Any:
    """Поднять модель из локального каталога и выставить пресет задержки.

    Сети здесь нет по построению: сначала `check_model_dir`, потом в mlx-audio
    уходит `pathlib.Path`, а не строка, которую библиотека сочла бы репозиторием.
    """
    if preset not in PRESETS:
        raise ValueError(f"пресет {preset!r} неизвестен; есть: {', '.join(PRESETS)}")
    problem = check_model_dir(path)
    if problem:
        raise ModelUnavailable(problem)
    try:
        from mlx_audio.vad import load
    except ImportError as e:
        raise ModelUnavailable(f"нет пакета mlx-audio — {INSTALL_RECIPE}") from e
    try:
        model = load(pathlib.Path(path), strict=True)
        model.set_streaming_config(preset)
    except MemoryError as e:
        raise ModelUnavailable(f"модели в {path} не хватило памяти ({e}) — закрыть тяжёлые "
                               f"приложения или взять 8-битные веса") from e
    except (TypeError, AttributeError) as e:
        # дрейф API пакета — не битые веса: рецепт версии, а не загрузки (круг 2, DS I2)
        raise ModelUnavailable(f"mlx-audio ответил не так, как {MLX_AUDIO_VERSION} "
                               f"({type(e).__name__}: {e}) — {INSTALL_RECIPE}") from e
    except Exception as e:  # noqa: BLE001 — дверь «каталог → модель»: любой отказ библиотеки уходит одним типом с рецептом
        # каталог прошёл проверку формы (model_type, размер весов), а тензоры не той
        # архитектуры или закачка оборвана выше порога — рецепт, а не трассировка mlx (DS I4)
        raise ModelUnavailable(f"модель в {path} не поднялась ({type(e).__name__}: {e}) — "
                               f"скачать заново: {fetch_recipe(pathlib.Path(path))}") from e
    return model


def to_segments(raw: Iterable[_Segment], prefix: str = "nem") -> list[dict]:
    """Сегменты mlx-audio → формат бенча и стенограммы.

    Метки — «nem0», «nem1»…: номер слота модели, а не человек. Имена по-прежнему
    расставляет другой слой. Пустые и вывернутые сегменты выбрасываются.
    """
    out = [{"start": round(float(s.start), 3), "end": round(float(s.end), 3),
            "speaker": f"{prefix}{int(s.speaker)}"}
           for s in raw if float(s.end) > float(s.start)]
    return sorted(out, key=lambda s: (s["start"], s["speaker"]))


#: Порог склейки кусков одного голоса, секунды. НАЗНАЧЕН, а не измерен: модель ещё
#: ни разу не прогонялась. Замер — первый прогон на Mac (№443, DS M1 круга 1 по #648).
MERGE_GAP_S = 0.0


def merge_same_speaker(segments: list[dict], gap: float = MERGE_GAP_S) -> list[dict]:
    """Склеить куски одного голоса, разрезанные границей чанка.

    Потоковый выход закрывает сегмент на краю каждого чанка: минута речи одного
    человека при чанке 1.04 с приходит полусотней кусков. Для DER это не ошибка,
    для стенограммы и счётчика смен говорящего — шум. Куски одного голоса
    склеиваются, если между ними не больше `gap` секунд; чужие голоса не
    трогаются, перекрытия разных голосов сохраняются как есть.
    """
    by_speaker: dict[str, list[dict]] = {}
    for seg in sorted(segments, key=lambda s: s["start"]):
        runs = by_speaker.setdefault(seg["speaker"], [])
        if runs and seg["start"] - runs[-1]["end"] <= gap + 1e-6:
            runs[-1]["end"] = max(runs[-1]["end"], seg["end"])
        else:
            runs.append(dict(seg))
    merged = [seg for runs in by_speaker.values() for seg in runs]
    return sorted(merged, key=lambda s: (s["start"], s["speaker"]))


def diarize_file(model: Any, audio: np.ndarray, sample_rate: int) -> list[dict]:
    """Вся запись одним вызовом (пресет выставлен при загрузке модели).

    Многоканальный звук сводится в моно, частота приводится моделью.
    """
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    result = model.generate(audio, sample_rate=sample_rate)
    return merge_same_speaker(to_segments(result.segments))


class NemotronStream:
    """Живой поток: PCM кусками любой длины → сегменты с абсолютным временем.

    Тот же контракт, что понадобится демону: `feed` на каждый блок звука,
    `close` в конце встречи (сбросить хвост, который модель держала ради
    правого контекста). Состояние — кэш голосов — живёт только в объекте.
    """

    def __init__(self, model: Any, threshold: float = 0.5):
        self._model = model
        self._threshold = threshold
        self._state = model.init_streaming_state()

    def feed(self, pcm: np.ndarray) -> list[dict]:
        pcm = np.asarray(pcm, dtype=np.float32)
        if pcm.ndim != 1:
            raise ValueError("поток ждёт моно: один канал, одномерный массив")
        result, self._state = self._model.feed(
            pcm, self._state, SAMPLE_RATE, threshold=self._threshold)
        return to_segments(result.segments)

    def close(self) -> list[dict]:
        result, self._state = self._model.feed(
            np.zeros(0, dtype=np.float32), self._state, SAMPLE_RATE,
            final=True, threshold=self._threshold)
        return to_segments(result.segments)
