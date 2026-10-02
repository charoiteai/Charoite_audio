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

Где работает. Пересборка стенограммы после встречи зовёт движок за флагом
`sufler.diarize_backend: nemotron` (№473) — процессом интерпретатора, в котором
стоит mlx-audio: в Python приложения mlx нет, бандл подписан. Интерпретатор —
окружение, которое ставит `scripts/install_engine.py nemotron` (`engine_python`,
№474), или явный путь `sufler.nemotron_python`. Протокол живёт в этом файле
с обеих сторон: `diarize_in_env` — сторона пересборки (дверь `foreign_python`),
`main()` — сторона движка (JSON
в stdout, `EXIT_ENGINE_UNAVAILABLE`, если движку нечем работать). Отказ или
сбой движка пересборка не прячет: голоса размечает sherpa, шапка стенограммы
говорит об откате, а причина с путями машины — в журнале разбора (№495). Живой контур и демон модуль не зовут; бенч
(`scripts/diar_bench.py --engine nemotron…`) зовёт его импортом.

Только Apple Silicon. Инференс — MLX-порт из пакета mlx-audio
(`mlx-community/Nemotron-3-Diarization`). В зависимости приложения пакет не
входит: его ставит установщик движка в своё окружение (`INSTALL_RECIPE`);
импорт mlx ленивый, поэтому модуль импортируется на любой машине, а чистые
функции проверяются в CI.

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

import argparse
import importlib.metadata
import importlib.util
import json
import math
import os
import pathlib
import re
import shlex
import sys
import wave
from typing import TYPE_CHECKING, Any, Callable, Iterable, Protocol

if TYPE_CHECKING:
    # numpy нужен только стороне движка — её функции импортируют его сами. Сторону
    # вызывающего (пробу, `engine_interpreter`, `install_command`) импортирует доктор,
    # которого обещано запускать любым Python: numpy на верху модуля валил его
    # `ModuleNotFoundError` ещё до разбора аргументов (№490).
    import numpy as np

# Сторона движка запускается путём к файлу под PYTHONSAFEPATH: каталог скрипта
# в sys.path сам не попадает, а соседи по src/ нужны (рецепт модуля под src/).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import foreign_python  # noqa: E402
from charoite_paths import code_root, harden_umask, inside_app_bundle  # noqa: E402
from exit_codes import EXIT_ENGINE_UNAVAILABLE  # noqa: E402

SAMPLE_RATE = 16000

#: `model_type` в config.json чекпойнта mlx-audio (convert.py пишет asdict(config)).
MODEL_TYPE = "nemotron_diarization"

#: Метка голоса в ответе движка: «nem0», «nem1»… — номер слота модели, не человек.
SPEAKER_PREFIX = "nem"

#: Пресеты задержки NVIDIA (входной буфер, без вычислений и окна STFT):
#: offline — 30.4 с, low — 1.04 с, very_low — 0.64 с, ultra_low — 0.32 с.
PRESETS = ("offline", "low", "very_low", "ultra_low")

HF_REPO = "mlx-community/Nemotron-3-Diarization"
#: Версия — та, по коду которой проверен стык (`load`, `set_streaming_config`, `feed`):
#: дрейф API иначе выяснился бы только на ручном прогоне (круг 1 по #648, DS M2).
#: `availability` сверяет её с установленной (круг 2, DS I2).
MLX_AUDIO_VERSION = "0.5.6"
#: Окружение движка ставит установщик продукта (№474): своя копия интерпретатора
#: и пакеты из `requirements-nemotron.lock` с хешами. Команду целиком — каким
#: интерпретатором и с каким корнем данных звать — собирает `install_command(root)`
#: у того, кто корень знает: пересборка, доктор, `--check`. Сторона движка корня
#: не знает и команды не печатает: её текст — причина, а команду к отказу
#: дописывает вызывающий (входной круг 2 по №489, I1). Бенч гоняет движок в своём
#: процессе, ему пакет ставится в .venv разработчика.
#: Python приложения внутри бандла: им ставится окружение движка (копия — его).
APP_PYTHON = "Charoite.app/Contents/Resources/python/bin/python3"
INSTALL_RECIPE = ("поставьте окружение движка установщиком продукта (бенч в .venv: "
                  f'.venv/bin/pip install "mlx-audio=={MLX_AUDIO_VERSION}"; только Apple Silicon)')
#: Указатель вместо команды — там, где текст уходит в стенограмму: её пересылают людям,
#: а команда несёт пути машины (корень данных, код), то есть имя учётки (выходной круг 1
#: по №489, M3). Команду целиком печатают доктор и `--check`.
INSTALLER_POINTER = ("команда установки — docs/DIARIZATION.md, раздел Nemotron; доктор печатает её "
                     "целиком, если запущен с CHAROITE_ROOT=<папка данных>")

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


def engine_dir(root: pathlib.Path) -> pathlib.Path:
    """Где живёт окружение движка: своя копия интерпретатора python-build-standalone
    с пакетами из лока (№474). В корне данных, а не рядом с бандлом: копия не
    зависит от того, где лежит и как обновляется приложение. Раскладку движка —
    веса и окружение — знает этот модуль; установщик берёт пути отсюда."""
    return root / "engines" / "nemotron"


def engine_python(root: pathlib.Path) -> pathlib.Path:
    """Интерпретатор установленного окружения — умолчание для пустого
    `sufler.nemotron_python`."""
    return engine_dir(root) / "python" / "bin" / "python3"


def app_python() -> str:
    """Python приложения рядом с кодом — или "", если код лежит не в бандле.

    Команду печатают разные процессы: пересборка, доктор из терминала, `--check`
    установщика. `sys.executable` любого из них годится не всегда: установщик
    примет только переносимую сборку той версии, под которую собран лок, то есть
    Python приложения. Код внутри бандла лежит в `Contents/Resources/charoite`,
    Python приложения — рядом, в `Contents/Resources/python`: его и называем.
    Вне бандла (код из клона) Python приложения отсюда не узнать, а угадывать
    нельзя: чужой переносимый интерпретатор другой версии дал бы команду, которую
    установщик отклонит (выходные круги 1–2 по №474)."""
    code = code_root(__file__)
    bundled = code.parent / "python" / "bin" / "python3"
    return str(bundled) if code.parent.name == "Resources" and bundled.is_file() else ""


def install_command(root: pathlib.Path) -> str:
    """Команда установщика для человека: одно место для пересборки, доктора и `--check`.

    Корень данных — тот, с которым работает печатающий: установщик называет его
    дверью канона и без `CHAROITE_ROOT` отказывает (№489), поэтому корень идёт в
    саму команду. Python приложения известен — готовая строка; нет — та же строка
    с путём `APP_PYTHON` и просьбой подставить свой Charoite.app.

    Корень внутри бандла — не корень данных, а догадка процесса, запущенного из
    бандла без `CHAROITE_ROOT`. Команды тогда нет: строка, которую можно вставить
    в shell, поставила бы движок в подписанный `.app` — вместо неё слова, где взять
    корень (входной круг 2 по №489, критика 1 и M1)."""
    if inside_app_bundle(root):
        return ("корень данных не назван — установщику нужна папка данных: запустите его с "
                "CHAROITE_ROOT, равной папке данных (в приложении — «Папка данных» в Настройках)")
    # `-B`: python бандла из Терминала не пишет байткод в подписанный `.app` —
    # и до того, как установщик успеет выключить его сам (Opus C1, 0.88.1).
    script = [str(code_root(__file__) / "scripts" / "install_engine.py"), "nemotron"]
    prefix = f"CHAROITE_ROOT={shlex.quote(str(root))} "
    python = app_python()
    if python:
        return prefix + shlex.join([python, "-B", *script])
    return f"{prefix}{APP_PYTHON} -B {shlex.join(script)} (путь к Charoite.app — ваш)"


def fetch_recipe(target: pathlib.Path) -> str:
    """Рецепт разовой загрузки весов — печатается, но не выполняется. Команду
    установщика с корнем дописывает вызывающий (`install_command`)."""
    return f"установщиком продукта (вручную: .venv/bin/hf download {HF_REPO} --local-dir {target})"


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


def _branch(version: str) -> tuple[int, int] | None:
    """`0.5.7.dev0+g1a2b` → `(0, 5)`; не читается — None."""
    m = re.match(r"(\d+)\.(\d+)", version)
    return (int(m[1]), int(m[2])) if m else None


def version_verdict(installed: str | None) -> tuple[str | None, str | None]:
    """Установленный mlx-audio против проверенного: `(отказ, предупреждение)`.

    Отказ — метаданных дистрибутива нет (версию не сверить) или другая ветка
    `major.minor`: в 0.x это ломающий шаг. Предупреждение — та же ветка, другой
    патч, dev- или локальная сборка: стык, скорее всего, тот же, прогон идёт, а
    рецепт на случай отказа `load` назван заранее. Строгое равенство отказывало
    любой сборке из git и `pip install -e` без выхода (выходной круг 3 по #648,
    DS I2). Политика одна — её читает `availability`, а `load_model` при отказе
    загрузки называет тот же рецепт.
    """
    if installed is None:
        return (f"mlx-audio стоит без метаданных дистрибутива — версию не сверить, стык "
                f"проверен на {MLX_AUDIO_VERSION}: {INSTALL_RECIPE}", None)
    if installed == MLX_AUDIO_VERSION:
        return None, None
    if _branch(installed) is None or _branch(installed) != _branch(MLX_AUDIO_VERSION):
        return f"mlx-audio {installed}, стык проверен на {MLX_AUDIO_VERSION} — {INSTALL_RECIPE}", None
    return None, (f"mlx-audio {installed}: стык проверен на {MLX_AUDIO_VERSION}, ветка та же — "
                  f"прогон идёт; откажет загрузка — {INSTALL_RECIPE}")


def _stderr(line: str) -> None:
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def availability(path: pathlib.Path, warn: Callable[[str], None] = _stderr) -> str | None:
    """Всё, чего не хватает для прогона, одним ответом — до долгой работы.

    None — пакет найден и каталог весов годен. Иначе — каждая проблема своей
    строкой с рецептом: человек ставит всё за один заход, а не по одной ошибке.
    Версия той же ветки, но не та, — не проблема, а строка в `warn`.
    """
    problems = []
    if importlib.util.find_spec("mlx_audio") is None:
        problems.append(f"нет пакета mlx-audio — {INSTALL_RECIPE}")
    else:
        try:
            installed = importlib.metadata.version("mlx-audio")
        except importlib.metadata.PackageNotFoundError:
            installed = None
        refusal, warning = version_verdict(installed)
        if refusal:
            problems.append(refusal)
        if warning:
            warn(warning)
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
        # класс исключения не различает дрейф API пакета и веса не той формы: оба
        # рецепта, человек выбирает по факту (выходной круг 3 по #648, критика DS 1)
        raise ModelUnavailable(f"mlx-audio ответил не так, как {MLX_AUDIO_VERSION} "
                               f"({type(e).__name__}: {e}) — если стоит другая версия: "
                               f"{INSTALL_RECIPE}; если версия та, веса не той формы — "
                               f"скачать заново: {fetch_recipe(pathlib.Path(path))}") from e
    except Exception as e:  # noqa: BLE001 — дверь «каталог → модель»: любой отказ библиотеки уходит одним типом с рецептом
        # каталог прошёл проверку формы (model_type, размер весов), а тензоры не той
        # архитектуры или закачка оборвана выше порога — рецепт, а не трассировка mlx (DS I4)
        raise ModelUnavailable(f"модель в {path} не поднялась ({type(e).__name__}: {e}) — "
                               f"скачать заново: {fetch_recipe(pathlib.Path(path))}") from e
    return model


def to_segments(raw: Iterable[_Segment], prefix: str = SPEAKER_PREFIX) -> list[dict]:
    """Сегменты mlx-audio → формат бенча и стенограммы.

    Метки — «nem0», «nem1»…: номер слота модели, а не человек. Имена по-прежнему
    расставляет другой слой. Пустые и вывернутые сегменты выбрасываются.
    """
    out = [{"start": round(float(s.start), 3), "end": round(float(s.end), 3),
            "speaker": f"{prefix}{int(s.speaker)}"}
           for s in raw if float(s.end) > float(s.start)]
    return sorted(out, key=lambda s: (s["start"], s["speaker"]))


#: Порог склейки кусков одного голоса, секунды. Измерен на Mac 28.09 (№443): в сыром
#: потоке 64–74 % пауз между кусками одного голоса ровно нулевые (разрезы чанков),
#: короче 0.08 с — меньше 1.2 %; с нулём поток совпадает с проходом по файлу (AMI
#: ES2004a: смены 306/305, DER 0.273/0.269). Паузы внутри реплики — политика
#: стенограммы при встраивании (№473), а не шов чанка.
MERGE_GAP_S = 0.0


def _us(seconds: float) -> int:
    """Секунды в целых микросекундах: пауза сравнивается с порогом без шума плавающей
    точки (1.3 - 1.0 — это 0.30000000000000004), и пауза, равная порогу, склеивается."""
    return round(seconds * 1_000_000)


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
        if runs and _us(seg["start"] - runs[-1]["end"]) <= _us(gap):
            runs[-1]["end"] = max(runs[-1]["end"], seg["end"])
        else:
            runs.append(dict(seg))
    merged = [seg for runs in by_speaker.values() for seg in runs]
    return sorted(merged, key=lambda s: (s["start"], s["speaker"]))


def diarize_file(model: Any, audio: np.ndarray, sample_rate: int) -> list[dict]:
    """Вся запись одним вызовом (пресет выставлен при загрузке модели).

    Многоканальный звук сводится в моно, частота приводится моделью.
    """
    import numpy as np
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
        import numpy as np
        pcm = np.asarray(pcm, dtype=np.float32)
        if pcm.ndim != 1:
            raise ValueError("поток ждёт моно: один канал, одномерный массив")
        result, self._state = self._model.feed(
            pcm, self._state, SAMPLE_RATE, threshold=self._threshold)
        return to_segments(result.segments)

    def close(self) -> list[dict]:
        import numpy as np
        result, self._state = self._model.feed(
            np.zeros(0, dtype=np.float32), self._state, SAMPLE_RATE,
            final=True, threshold=self._threshold)
        return to_segments(result.segments)

    @property
    def frames_processed(self) -> int:
        """Кадров разметки, выданных моделью: фронт — всё, что до него, размечено."""
        return int(self._state.frames_processed)


def frame_seconds(model: Any) -> float:
    """Длительность единицы `frames_processed` потока — родного кадра спектра: hop / частота.

    mlx-audio 0.5.x, `nemotron_diarization.StreamingState`: «Native 10 ms frames, before
    output downsampling». Энкодер прореживает вход в 8 раз, но счёт состояния и выход
    модели идут на шаге спектра; прежняя формула hop · 8 / частота читала поле конфига
    sortformer, которого у этой модели нет, и ошибалась бы в восемь раз (фикс A2 №478).
    Поля — из публичного `config.processor_config`, того же, что лежит в `config.json`
    весов (входной круг фикса, M1). Сверка единицы со звуком — `_check_front`.
    """
    proc = model.config.processor_config
    if proc.sampling_rate != SAMPLE_RATE:
        # Поток идёт на частоте хаба; модель другой частоты `feed` отвергла бы на первом
        # блоке, а кадр не был бы целым числом сэмплов потока, и сверка фронта лгала бы
        # (выходной круг фикса A2 №478, M2). Отказ — до рукопожатия, с причиной.
        raise ModelUnavailable(f"модель ждёт звук {proc.sampling_rate} Гц, поток идёт на {SAMPLE_RATE} Гц")
    return proc.hop_length / proc.sampling_rate


# ------------------------------------------------ живой поток (№478, `--stream`)

#: Версия протокола живого потока: JSON-строки в дескрипторе протокола.
STREAM_PROTO = 1
#: Блок, которым движок кормит поток, — в сэмплах (0,5 с, как бенч `nemotron-live`).
STREAM_STEP = SAMPLE_RATE // 2
#: Шаг опроса родителя сторожем потока, секунды (№540).
PARENT_POLL_S = 0.5


def parent_gone(parent_pid: int) -> bool:
    """Родителя нет. Прямой родитель — свой ответ; между ними обёртка без `exec` (скрипт в
    `sufler.nemotron_python`) — `getppid()` даёт её pid при живом демоне, и тогда жизнь
    демона проверяется сигналом 0 (финальный Opus, M1). Переподчинение launchd — ушёл."""
    ppid = os.getppid()
    if ppid == parent_pid:
        return False
    if ppid == 1:
        return True
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:          # pid занят чужим процессом — демона нет
        return True
    return False


def watch_parent(parent_pid: int, poll_s: float = PARENT_POLL_S) -> None:
    """Сторож родителя (№540): родителя нет (`parent_gone`) — процесс выходит сам, `os._exit`.

    Демон, убитый SIGKILL (приложение добивает повисший через 12 с), не даёт ни `finally`,
    ни `atexit`; на macOS нет PDEATHSIG. Читающий вход ребёнок умрёт и так — EOF, затем
    BrokenPipe на записи; сторож — для того, кто в этот момент вход не читает (загрузка
    модели, долгий `feed`). Нить берёт GIL раз в шаг: MLX отпускает его на время
    вычисления, чистый Python отдаёт каждые `sys.getswitchinterval()` (замер №540).
    Родитель передаётся явно: умри он до старта нити, `getppid()` уже 1, и сравнение
    «с тем, что было при старте» смерти бы не увидело."""
    import time

    import threads

    def run() -> None:
        # Ни строки перед выходом (финальный Opus, I1): stderr — файл в `logs/`, а диск,
        # из-за которого демона добили, повис бы и здесь; читать её всё равно некому.
        while not parent_gone(parent_pid):
            time.sleep(poll_s)
        os._exit(1)

    threads.spawn(run, name="nemotron-parent-watch", role="process",
                  detached="сторож родителя живёт, пока жив процесс движка")


def _emit_segments(segments: list[dict], frames: int, frame_s: float, emit, *, final: bool) -> None:
    """Сегменты блока — строками протокола. «Открыт» — конец упирается во фронт: речь
    ещё может продолжиться следующим блоком. Сравнение в целых кадрах: секунды
    сегментов округлены, а фронт кратен кадру (выходной круг входа 2, I3)."""
    for seg in segments:
        end_frames = round(seg["end"] / frame_s)
        emit({"type": "seg", "start": seg["start"], "end": seg["end"],
              "slot": int(seg["speaker"][len(SPEAKER_PREFIX):]),
              "open": (not final) and end_frames >= frames})


def _check_front(frames: int, fed: int, frame_s: float, *, final: bool) -> None:
    """Единица кадра против поданного звука — единственной величины, в которой нет
    сомнений. Модель не размечает звук, которого не получила, а финал размечает весь
    звук с точностью до кадра (замер на mlx-audio 0.5.6, четыре пресета, 0–31 с: на
    финале кадров ровно `fed // hop`). Нарушение — не та единица кадра: ребёнок умирает
    с числами в журнале, а не отдаёт тихо неверный фронт и неверное «открыт»
    (входной круг фикса A2 №478, I1 и M3). Счёт — в целых сэмплах: кадр модели — целое
    число сэмплов (частота модели равна частоте потока, иначе `feed` отказывает сам), и
    границы точные, без допуска плавающей точки (мутатор диапазона фикса)."""
    hop = round(frame_s * SAMPLE_RATE)
    covered = frames * hop
    if covered > fed:
        raise RuntimeError(f"фронт модели {frames * frame_s:.3f} с впереди поданного звука "
                           f"{fed / SAMPLE_RATE:.3f} с ({frames} кадров по {frame_s} с) — единица кадра не та")
    if final and fed - covered >= hop:
        raise RuntimeError(f"финал модели {frames * frame_s:.3f} с не покрыл поданный звук "
                           f"{fed / SAMPLE_RATE:.3f} с ({frames} кадров по {frame_s} с) — единица кадра не та")


def run_stream(stream: Any, *, frame_s: float, read, emit, step: int = STREAM_STEP) -> int:
    """Цикл живого потока: s16le со stdin блоками `step` → сегменты и фронт.

    Нечётный байт переносится в следующее чтение (как `TapStreamCapture._pump_file`):
    труба отдаёт куски любой длины, и звук без переноса съехал бы на байт молча.
    После каждого блока — фронт: `fed` — поданные сэмплы, `frames` — кадры,
    выданные моделью, `cpu_s` и `rss_mb` — цена процесса; до строк фронт
    сверяется со звуком (`_check_front`). EOF — хвост блока, `close()`, последний
    фронт с `final`.
    """
    import numpy as np
    need = step * 2
    buf = bytearray()
    fed = 0
    eof = False
    while not eof:
        data = read(65536)
        if data:
            buf += data
        else:
            eof = True
        while len(buf) >= need or (eof and len(buf) >= 2):
            take = min(need, len(buf) - len(buf) % 2)       # need чётно: целые сэмплы
            pcm = np.frombuffer(bytes(buf[:take]), dtype="<i2").astype(np.float32) / 32768.0
            del buf[:take]
            segments = stream.feed(pcm)
            fed += len(pcm)
            _check_front(stream.frames_processed, fed, frame_s, final=False)
            _emit_segments(segments, stream.frames_processed, frame_s, emit, final=False)
            emit({"type": "front", "fed": fed, "frames": stream.frames_processed, **_load()})
    segments = stream.close()
    _check_front(stream.frames_processed, fed, frame_s, final=True)
    _emit_segments(segments, stream.frames_processed, frame_s, emit, final=True)
    emit({"type": "front", "fed": fed, "frames": stream.frames_processed, "final": True, **_load()})
    return 0


def _load() -> dict:
    """Цена потока для журнала тени: CPU-секунды процесса движка (все нити), пик RSS
    (справка), текущий след памяти процесса и счётчики MLX. Время GPU сюда не входит —
    его меряет лабораторный опыт (выходной круг входа 2, критика 1).

    Давление macOS судит `phys_footprint`, а не RSS: буферы MLX в RSS не видны вовсе
    (опыт 30.09: 1 ГБ в MLX — RSS 530 МБ, след 1541 МБ), и пик `ru_maxrss` не убывает,
    поэтому доля кэша считается по `phys_mb` и `mlx_*_mb` (входной круг №478 B, память).
    Чего нет на этой машине — ключа нет: строка фронта не ломается."""
    import resource
    import time
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss      # macOS — байты
    out = {"cpu_s": round(time.process_time(), 2), "rss_mb": round(peak / 2**20)}
    phys = _phys_footprint()
    if phys is not None:
        out["phys_mb"] = round(phys / 2**20)
    out.update(_mlx_memory())
    return out


#: `proc_pid_rusage`, `RUSAGE_INFO_V0` — ровно эта структура: 16 байт uuid и десять uint64
#: (время пользователя и ядра, пробуждения, прерывания, pageins, wired, resident,
#: phys_footprint, начало и конец процесса), 96 байт. Старшие версии длиннее: с V2 при
#: буфере V0 ядро писало за его конец, и тесты падали сегфолтом в сборке мусора.
_RUSAGE_INFO_V0 = 0
_RUSAGE_FIELDS = ("user", "system", "pkg_idle", "interrupt", "pageins", "wired", "resident",
                  "phys_footprint", "start", "exit")


def _phys_footprint() -> int | None:
    """Текущий след памяти процесса в байтах (`ri_phys_footprint`); не macOS — None."""
    import ctypes
    import ctypes.util
    import os

    class _Info(ctypes.Structure):
        _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in _RUSAGE_FIELDS]

    path = ctypes.util.find_library("c")
    if not path:
        return None
    info = _Info()
    try:
        rc = ctypes.CDLL(path).proc_pid_rusage(os.getpid(), _RUSAGE_INFO_V0, ctypes.byref(info))
    except (OSError, AttributeError):
        return None
    return int(info.phys_footprint) if rc == 0 else None


def _mlx_memory() -> dict:
    """Счётчики аллокатора MLX в МБ: занято, кэш, пик (после загрузки модели пик
    сброшен — `serve_stream`). Только уже загруженный mlx: в процессе движка его подняла
    модель, а цена процесса не вправе тянуть mlx туда, где его нет (вызывающий, тесты).
    Нет mlx или функции — пустой словарь."""
    import sys
    mx = sys.modules.get("mlx.core")
    if mx is None:
        return {}
    out = {}
    for key, name in (("mlx_active_mb", "get_active_memory"), ("mlx_cache_mb", "get_cache_memory"),
                      ("mlx_peak_mb", "get_peak_memory")):
        fn = getattr(mx, name, None)
        if fn is not None:
            out[key] = round(fn() / 2**20)
    return out


def _reset_mlx_peak() -> None:
    import sys
    mx = sys.modules.get("mlx.core")
    if mx is None:
        return
    reset = getattr(mx, "reset_peak_memory", None)
    if reset is not None:
        reset()


def _set_cache_limit(limit_mb: int | None) -> dict:
    """Лимит кэша MLX до загрузки модели; поля для рукопожатия — заданный и прежний
    (`set_cache_limit` возвращает прежний). Без лимита — только прежний не узнать, пусто."""
    if limit_mb is None:
        return {}
    try:
        import mlx.core as mx
    except ImportError as e:          # окружение без mlx — тот же отказ «движка нет», что у модели
        raise ModelUnavailable(f"лимит кэша: mlx не импортируется ({e})") from e
    prev = mx.set_cache_limit(limit_mb * 2**20)
    return {"cache_limit_mb": limit_mb, "cache_limit_prev_mb": round(prev / 2**20)}


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise ValueError(text)
    return value


def _parent_pid(text: str) -> int:
    """pid родителя для сторожа: 0 и 1 — не родитель (у сироты `getppid()` равен 1, и
    сторож с таким pid не увидел бы смерти демона)."""
    value = int(text)
    if value <= 1:
        raise ValueError(text)
    return value


def _protocol_channel():
    """Протокол — в дубликат дескриптора 1, а сам дескриптор 1 — в stderr.

    Любая печать библиотек (прогресс загрузки, предупреждения) уходит в журнал
    ребёнка и не рвёт протокол; строки протокола — с записью на каждую строку,
    без блочной буферизации трубы (выходной круг входа 1, I1)."""
    import os
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(proto_fd, "w", buffering=1, encoding="utf-8")


def _read_stdin(n: int) -> bytes:
    import os
    return os.read(0, n)


def serve_stream(model_dir: pathlib.Path, preset: str, read: Callable[[int], bytes] = _read_stdin,
                 cache_limit_mb: int | None = None) -> int:
    """Сторона движка живого потока: модель, рукопожатие, цикл до EOF stdin. Протокол —
    ASCII: числа, слоты и имя пресета. `cache_limit_mb` — лимит кэша MLX до загрузки
    модели; рукопожатие объявляет его (замер памяти тени, №478 B)."""
    proto = _protocol_channel()

    def emit(message: dict) -> None:
        proto.write(json.dumps(message) + "\n")

    try:
        limit = _set_cache_limit(cache_limit_mb)
        model = load_model(model_dir, preset)
        frame_s = frame_seconds(model)
    except ModelUnavailable as e:
        _stderr(str(e).replace("\n", "; "))
        return EXIT_ENGINE_UNAVAILABLE
    _reset_mlx_peak()                  # пик MLX — потока, а не загрузки весов
    emit({"type": "ready", "proto": STREAM_PROTO, "sr": SAMPLE_RATE, "preset": preset,
          "frame_s": frame_s, "step": STREAM_STEP, **limit})
    return run_stream(NemotronStream(model), frame_s=frame_s, read=read, emit=emit)


# ------------------------------------------------ протокол пересборки (№473)

#: Файл движка для стороны вызывающего: путём к файлу, не `-m` — у чужого
#: интерпретатора свой sys.path, и имени модуля он не знает. Путь — от корня
#: кода, как у пересборки, которую зовёт демон.
SCRIPT = code_root(__file__) / "src" / "diarize_nemotron.py"
_LABEL = re.compile(rf"{SPEAKER_PREFIX}(\d+)")


def read_wav(path: pathlib.Path) -> tuple[np.ndarray, int]:
    """16-битный PCM → float32 в [-1, 1), как `load_wav` пересборки; каналы — в моно."""
    import numpy as np
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: не 16-битный PCM ({8 * w.getsampwidth()} бит)")
        sr, channels = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    return pcm.reshape(-1, channels).mean(axis=1, dtype=np.float32) / 32768.0, sr


def main(argv: list[str] | None = None) -> int:
    """Сторона движка: голоса одной записи — JSON `{"segments": [...]}` в stdout.

    Порядок: аргументы, затем проверка движка (нечем работать — причина одной
    строкой в stderr и `EXIT_ENGINE_UNAVAILABLE`, запись не читается вовсе), затем
    звук и модель. Любой другой сбой — трассировка и код 1: для вызывающего это
    «движок упал», не «движка нет».
    """
    harden_umask()   # то, что создаст mlx-audio (временный каталог filelock), — только владельцу
    ap = argparse.ArgumentParser(
        description="Nemotron: голоса одной записи в JSON — сторона движка для пересборки "
                    "стенограммы (её зовёт diarize_in_env процессом интерпретатора с mlx-audio).")
    ap.add_argument("wav", type=pathlib.Path, nargs="?",
                    help="запись канала собеседников, 16-битный WAV (без --probe обязательна)")
    ap.add_argument("--model", type=pathlib.Path, required=True,
                    help="каталог весов (config.json и веса) — models/diar/nemotron корня данных")
    ap.add_argument("--probe", action="store_true",
                    help="только проверить, чем работать: JSON {\"mlx_audio\": версия}, веса не грузятся")
    ap.add_argument("--stream", action="store_true",
                    help="живой поток: s16le 16 кГц со stdin, JSON-строки протокола в stdout до EOF")
    ap.add_argument("--preset", choices=PRESETS, default="low",
                    help="задержка живого потока (--stream): low — 1.04 с")
    ap.add_argument("--cache-limit-mb", type=_non_negative_int, default=None,
                    help="лимит кэша MLX живого потока (--stream), МБ; без флага — как у mlx")
    ap.add_argument("--parent-pid", type=_parent_pid, default=None,
                    help="pid родителя живого потока (--stream): его не стало — процесс выходит сам")
    args = ap.parse_args(argv)
    if args.stream and args.parent_pid is not None:
        watch_parent(args.parent_pid)      # до проверки движка и загрузки модели
    if not (args.probe or args.stream) and args.wav is None:
        ap.error("нужна запись (или --probe, или --stream)")
    problem = availability(args.model)
    if problem:
        _stderr(problem.replace("\n", "; "))
        return EXIT_ENGINE_UNAVAILABLE
    if args.stream:
        return serve_stream(args.model, args.preset, cache_limit_mb=args.cache_limit_mb)
    if args.probe:
        print(json.dumps({"mlx_audio": importlib.metadata.version("mlx-audio")}))
        return 0
    audio, sr = read_wav(args.wav)
    try:
        model = load_model(args.model, "offline")
    except ModelUnavailable as e:
        _stderr(str(e).replace("\n", "; "))
        return EXIT_ENGINE_UNAVAILABLE
    print(json.dumps({"segments": diarize_file(model, audio, sr)}))
    return 0


def parse_segments(payload: dict) -> list[tuple[float, float, int]]:
    """Ответ движка → `(start, end, N)`; отступление от протокола — ValueError."""
    raw = payload.get("segments")
    if not isinstance(raw, list):
        raise ValueError("нет списка segments")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"отрезок не объект: {item!r}")
        s, e, label = item.get("start"), item.get("end"), item.get("speaker")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                   for v in (s, e)) or not 0 <= s < e:
            raise ValueError(f"границы отрезка: {item!r}")
        m = _LABEL.fullmatch(label) if isinstance(label, str) else None
        if m is None:
            raise ValueError(f"метка голоса: {label!r}")
        out.append((float(s), float(e), int(m.group(1))))
    return out


def engine_interpreter(setting: str, root: pathlib.Path) -> tuple[str, str]:
    """Чем запускать движок: `(путь, "")` или `("", причина отказа)`.

    Настройка задана — она (нет файла — скажет сам запуск); пустая — установленное
    окружение `engine_python(root)`, если оно есть. Одно место выбора для обеих
    дверей движка — разметки и пробы.

    Ведущая `~` раскрывается: `subprocess` её не понимает, и `~/…/bin/python` молча
    сводился к отказу «нет интерпретатора» (№503). Только `os.path.expanduser`: `Path`
    нормализовал бы запись («./python» → «python» ищется по PATH), а `resolve` развернул
    бы симлинк `bin/python` окружения до базового интерпретатора без его пакетов."""
    python = setting.strip()
    if python:
        return os.path.expanduser(python), ""
    installed = engine_python(root)
    if installed.exists():
        return str(installed), ""
    return "", f"окружение движка не установлено ({engine_dir(root)})"


def _with_remedy(out: foreign_python.Outcome, setting: str, remedy: str) -> foreign_python.Outcome:
    """К отказу «нечем работать» — чем лечить, если лечит установщик.

    Сторона движка корня не знает и лечения не печатает. UNAVAILABLE при пустом
    ключе лечится (пере)установкой окружения; при заданном `sufler.nemotron_python`
    ключ главнее установленного окружения, и установщик его не вылечит (выходной
    круг 1 по №489, I1). FAILED — падение, а не нехватка: переустановка его не обещает."""
    if out.kind != foreign_python.UNAVAILABLE or setting.strip():
        return out
    return foreign_python.Outcome(foreign_python.UNAVAILABLE, reason=f"{out.reason} — {remedy}")


def probe_in_env(setting: str, *, root: pathlib.Path, timeout: float = 60.0) -> foreign_python.Outcome:
    """Сторона вызывающего: готов ли движок — без записи и без загрузки весов.

    OK — `{"mlx_audio": версия}`; UNAVAILABLE — окружения нет или ему нечем
    работать (причина со стороны движка: пакет, версия, каталог весов — с командой
    установщика). Её зовут доктор и `--check` установщика."""
    remedy = f"окружение ставит: {install_command(root)}"
    python, refusal = engine_interpreter(setting, root)
    if refusal:
        return _with_remedy(foreign_python.Outcome(foreign_python.UNAVAILABLE, reason=refusal), setting, remedy)
    out = foreign_python.run_json(python, SCRIPT, ["--probe", "--model", str(model_dir(root))],
                                  timeout=timeout)
    if out.ok and not isinstance(out.payload.get("mlx_audio"), str):
        return foreign_python.Outcome(foreign_python.FAILED,
                                      reason=f"проба движка не по протоколу: {out.payload!r}")
    return _with_remedy(out, setting, remedy)


def diarize_in_env(setting: str, wav: pathlib.Path, *, root: pathlib.Path,
                   timeout: float) -> foreign_python.Outcome:
    """Сторона пересборки: разметить запись процессом интерпретатора движка.

    Интерпретатор — `setting` (`sufler.nemotron_python`), а пустая настройка —
    установленное окружение `engine_python(root)`; веса — `model_dir(root)`.
    Раскладку движка знает этот модуль, вызывающий передаёт только корень.

    OK — сегменты `(start, end, N)`, N — слот модели; UNAVAILABLE — движка на этой
    машине нет (настройка пуста и окружение не установлено) или ему нечем работать;
    FAILED — движок упал, ответил не по протоколу или явного интерпретатора нет на
    диске (так размечает дверь `foreign_python`). mlx в процесс вызывающего не попадает: здесь только
    запуск и разбор JSON.
    """
    python, refusal = engine_interpreter(setting, root)
    if refusal:
        return _with_remedy(foreign_python.Outcome(foreign_python.UNAVAILABLE, reason=refusal), setting,
                            INSTALLER_POINTER)
    out = foreign_python.run_json(python, SCRIPT, [str(wav), "--model", str(model_dir(root))],
                                  timeout=timeout)
    if not out.ok:
        return _with_remedy(out, setting, INSTALLER_POINTER)
    try:
        return foreign_python.Outcome(foreign_python.OK, payload=parse_segments(out.payload))
    except ValueError as e:
        return foreign_python.Outcome(foreign_python.FAILED,
                                      reason=f"ответ движка не по протоколу: {e}")


if __name__ == "__main__":
    sys.exit(main())
