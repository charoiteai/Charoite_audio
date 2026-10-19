#!/usr/bin/env python3
"""Модель диаризации одной командой — вместо «найдите и экспортируйте ONNX».

`README` обещает метки «Собеседник 1/2/…» по голосам, а демон включает трекер
только при наличии `models/diar/embedding.onnx`. Файл в поставку не входит:
STT-модель тянется сама при первом запуске, а эмбеддер до сих пор предлагалось
найти в исследовательском репозитории 3D-Speaker и экспортировать в ONNX
самостоятельно. То есть ключевая фича продукта у нового пользователя просто не
работала, и починить это можно было только чтением документации.

    .venv/bin/python scripts/get_models.py --list             # что есть и откуда
    .venv/bin/python scripts/get_models.py --diar             # эмбеддинги, затем сегментация
    .venv/bin/python scripts/get_models.py --diar --model eres2net-en
    .venv/bin/python scripts/get_models.py --diar --check     # проверить оба файла

О сети начистоту. Этот скрипт ходит наружу — и ходит:

    * только когда его запустили: руками из терминала или кнопкой установки
      модели голосов в приложении (`--diar`); демон его не зовёт;
    * на адрес, который печатается ДО скачивания — в терминале и в канале
      (`| tee`, лог) одинаково: stdout буферизуется строкой (№481).
      `--diar` без `--url` и `--dest` ходит дважды: эмбеддинги с Hugging Face,
      затем сегментация со страницы релизов sherpa-onnx;
    * один раз: дальше модели лежат файлами и работают офлайн.

Он не единственный выход в сеть: веса выбранного бэкенда распознавания качает
его библиотека при первом запуске, окружение движка Nemotron ставит
`scripts/install_engine.py`, облачный слой — по выбору; все пути перечислены в
PRIVACY.md.

`--check` не открывает соединений вовсе. Обещание «никаких сетевых вызовов,
кроме вашего localhost» в PRIVACY.md относится к работе продукта; разовая
загрузка модели описана там же отдельным пунктом — как и загрузка STT.

Лицензия моделей — на стороне upstream (3D-Speaker, Apache-2.0; зеркала ONNX
собраны для sherpa-onnx). Скрипт печатает источник, принимает условия
пользователь.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import os
import pathlib
import shutil
import sys
import time
import urllib.error
import urllib.request

# Вставка — только чтобы импортировать сам канон путей: модели качаем в
# корень ДАННЫХ, а где он — знает канон (CHAROITE_ROOT), не этот файл.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import file_locks  # noqa: E402
from charoite_paths import resolve_root  # noqa: E402

# Минимальный разумный размер: ERes2Net в ONNX — десятки мегабайт. Всё, что
# меньше, — обрыв закачки, HTML-страница или подсунутый не тот файл.
MIN_BYTES = 5 * 1024 * 1024
ONNX_MAGIC = b"\x08"        # protobuf field 1 (ir_version) — первый байт .onnx

MIRROR = "https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/main"
UPSTREAM = "https://github.com/modelscope/3D-Speaker"
# Релизы sherpa-onnx, а не зеркало на Hugging Face: HF отдаёт этот файл
# без Content-Length и рвёт закачку на середине (проверено — приходило
# 2.1-2.5 МБ вместо шести, и ONNX не парсился). GitHub отдаёт архив целиком.
SEG_MIRROR = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
              "speaker-segmentation-models")
SEG_UPSTREAM = "https://github.com/pyannote/pyannote-audio"


@dataclasses.dataclass(frozen=True)
class Model:
    url: str
    size_mb: int
    note: str
    source: str = UPSTREAM
    #: sha256 файла по этому URL. Ссылки указывают на `resolve/main` и на
    #: релизы — то есть на подвижные цели: владелец зеркала (или тот, кто
    #: увёл его аккаунт) подменяет файл, не меняя URL. Модель потом слушает
    #: все встречи, а проверка формата ловит только обрыв закачки. Тот же
    #: урок уже применён к интерпретатору (build_embedded_python.sh),
    #: моделей он не коснулся — аудит 16.08.
    sha256: str = ""


MODELS = {
    "eres2net-base": Model(
        url=f"{MIRROR}/3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx",
        sha256="e2d2048292e055f7b61cdec3db010503f35369b245bf0b3bbad021c9a91e4053",
        size_mb=40,
        note="дефолт: ERes2Net base, обучена на 200k говорящих — ровнее всего "
             "держит смешанные встречи",
    ),
    "eres2net-en": Model(
        url=f"{MIRROR}/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx",
        sha256="c59158379255ad66e161679cca6af8d52d51e389e3224ab7d7a7baae295c2db5",
        size_mb=27,
        note="легче и быстрее, VoxCeleb (английский корпус); брать, если "
             "мало памяти или встречи в основном англоязычные",
    ),
    "eres2netv2": Model(
        url=f"{MIRROR}/3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx",
        sha256="bf1a75b9930474cf3389ef415e6e5d38ca96fea4a3a00f7e301d080a58ee2239",
        size_mb=71,
        note="v2, крупнее и точнее на близких голосах; медленнее на чанк",
    ),
}
DEFAULT = "eres2net-base"

# Модель сегментации: «кто говорит в этот момент» до всякой кластеризации.
# Наш собственный проход её не использует — он режет речь по каналам и
# косинусам, — но sherpa-onnx умеет полноценную диаризацию, и сравнить одно
# с другим без этой модели нельзя.
SEGMENTATION = {
    "pyannote-3.0": Model(
        url=f"{SEG_MIRROR}/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
        sha256="24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
        size_mb=7,
        note="pyannote segmentation 3.0 в экспорте sherpa-onnx: границы речи и "
             "перекрытия говорящих",
        source=SEG_UPSTREAM,
    ),
}
SEG_DEFAULT = "pyannote-3.0"

# Распознавание речи для языков, где Whisper не лучший выбор.
#
# GigaAM закрывает русский, Parakeet — английский, а китайские встречи до сих
# пор шли на whisper-large-v3-turbo: мультиязычная модель, которая на китайском
# уступает специализированным. SenseVoice Small работает через тот же
# sherpa-onnx, что уже стоит ради диаризации, — новой зависимости не появляется.
#
# Качаем два файла напрямую, а не архив релиза: в нём лежат и fp32, и int8, и
# тестовые wav — гигабайт вместо 239 МБ ради того же самого.
STT_MIRROR = ("https://huggingface.co/csukuangfj/"
              "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main")
STT_UPSTREAM = "https://github.com/FunAudioLLM/SenseVoice"
#: sha256 спутника tokens.txt (не LFS, сумма снята вручную 16.08)
TOKENS_SHA256 = "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc"

STT_MODELS = {
    "sensevoice": Model(
        url=f"{STT_MIRROR}/model.int8.onnx",
        sha256="c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51",
        size_mb=228,
        note="SenseVoice Small (int8): китайский и ещё четыре восточноазиатских "
             "языка одной моделью; ставится вместе с tokens.txt",
        source=STT_UPSTREAM,
    ),
}
STT_DEFAULT = "sensevoice"

# Веса движка Nemotron 3 Diarization (MLX-порт, №474). Ставит их установщик
# движка (`scripts/install_engine.py nemotron`), а не флаг этого скрипта: веса
# без окружения с mlx-audio бесполезны. Модель из двух файлов, как sensevoice:
# веса — запись `Model`, config.json — спутник со своей суммой. Ревизия в URL
# закреплена: `resolve/main` — подвижная цель, а сумма ловила бы законное
# обновление как подмену. Куда класть — решает модуль движка (`model_dir`).
# Лицензия весов — NVIDIA OpenMDW 1.1, в репозиторий они не кладутся.
NEMOTRON_REVISION = "59ed2dbfc1346dcea9d423c71306a3a2499c568f"
NEMOTRON_MIRROR = f"https://huggingface.co/mlx-community/Nemotron-3-Diarization/resolve/{NEMOTRON_REVISION}"
NEMOTRON = Model(
    url=f"{NEMOTRON_MIRROR}/model.safetensors",
    sha256="21e8427d1795c9c46c5800f56b16061734ffd0dcadd71d9bcf0b4d6ef7261da5",
    size_mb=190,
    note="Nemotron 3 Diarization (MLX): до 8 голосов, два голоса в одном кадре",
    source="https://huggingface.co/nvidia/Nemotron-3-Diarization",
)
#: sha256 спутника config.json (не LFS, сумма снята вручную 28.09 с файла ревизии выше)
NEMOTRON_CONFIG_SHA256 = "f215091252bc54f8dcc7d682962a659e03a6bcb26d1c86e4728b8c6730130eb4"

#: Файлы весов Nemotron: имя, адрес, размер МБ, sha256. Одна таблица и на проверку
#: (`nemotron_missing`), и на загрузку (`fetch_nemotron`) — чтобы план считал по
#: той же проверке, по которой решает установка.
NEMOTRON_FILES = (
    ("config.json", f"{NEMOTRON_MIRROR}/config.json", 1, NEMOTRON_CONFIG_SHA256),
    ("model.safetensors", NEMOTRON.url, NEMOTRON.size_mb, NEMOTRON.sha256),
)

#: Лицензия весов Nemotron: имя и ссылка. Веса в репозиторий не кладутся — человек
#: скачивает их сам и принимает условия; установщик печатает эту строку в блоке
#: сети до соединения, а `--plan` кладёт её в JSON.
NEMOTRON_LICENSE = ("NVIDIA OpenMDW 1.1",
                    "https://huggingface.co/nvidia/Nemotron-3-Diarization/blob/main/LICENSE")


def nemotron_missing(dest: pathlib.Path) -> list[tuple[str, str, int, str]]:
    """Файлы весов, которые `fetch_nemotron` будет качать: нет файла или его
    сумма не та. Той же проверкой считает и план (`--plan`): структура каталога
    (`diarize_nemotron.check_model_dir`) ловит только форму и пропустила бы
    битый `model.safetensors` при правдоподобном `config.json` — план обещал бы
    0, а установка тянула 190 МБ.

    `OSError` при чтении суммы — «файла нет в рабочем виде»: недостающий, без
    исключения наружу (иначе нечитаемый файл обернулся бы в `--plan` строкой
    `{"error": …}` вместо плана)."""
    missing = []
    for name, url, size_mb, sha in NEMOTRON_FILES:
        target = dest / name
        try:
            if target.is_file() and _digest(target) == sha:
                continue
        except OSError:
            pass
        missing.append((name, url, size_mb, sha))
    return missing


def fetch_nemotron(dest: pathlib.Path) -> None:
    """Веса Nemotron в каталог `dest` — недостающие файлы, каждый со своей суммой.

    Что качать, решает `nemotron_missing` — та же проверка, по которой считает
    план. Файл на месте и с той же суммой не качается заново: повтор установки
    после сбоя окружения не тянет 190 МБ второй раз."""
    missing = {name for name, *_ in nemotron_missing(dest)}
    for name, url, size_mb, sha in NEMOTRON_FILES:
        target = dest / name
        if name in missing:
            download(url, target, size_mb, onnx=False, sha256=sha)
        else:
            print(f"уже на месте: {target}")


def seg_target(root: pathlib.Path | None = None) -> pathlib.Path:
    """Куда кладём модель сегментации (без аргумента — корень данных у канона)."""
    return (root or resolve_root(__file__)) / "models" / "diar" / "segmentation.onnx"


def stt_target(root: pathlib.Path | None = None) -> pathlib.Path:
    """Куда кладём модель распознавания (рядом ляжет tokens.txt)."""
    return (root or resolve_root(__file__)) / "models" / "stt" / "sensevoice.onnx"


def diar_target(root: pathlib.Path | None = None) -> pathlib.Path:
    """Путь, по которому модель ищет демон (src/daemon.py)."""
    return (root or resolve_root(__file__)) / "models" / "diar" / "embedding.onnx"


def check(path: pathlib.Path, min_bytes: int = MIN_BYTES) -> str | None:
    """Что не так с этим файлом. None — модель на месте и похожа на себя.

    Проверка нарочно дешёвая и офлайновая: существование, размер, ONNX-магия.
    Загружать модель в sherpa-onnx здесь не нужно — это делает демон, а
    ответ «подойдёт ли файл» человек должен получать мгновенно и без сети.

    `exists`, `open` и `stat` — под `try/except OSError`: каталог или нечитаемый
    файл там, где ждали модель, это «модели нет в рабочем виде», а не исключение
    наружу. `--plan` зовёт `check` для набора голосов и обязан выдать одну строку
    JSON, а не `{"error": …}` с кодом 1."""
    fix = ("поставить: .venv/bin/python scripts/get_models.py --diar "
           "(модели: --list)")
    try:
        if not path.exists():
            return f"модели диаризации нет ({path}) — {fix}"
        # Сначала «это вообще ONNX?», потом «целиком ли скачалось»: HTML-страница
        # логина весит килобайты, и жаловаться на её размер — путать причину.
        with path.open("rb") as f:
            head = f.read(1)
        size = path.stat().st_size
    except OSError as e:
        return f"{path} не читается ({e}) — {fix}"
    if head != ONNX_MAGIC:
        return (f"{path.name} не похож на .onnx (первый байт {head!r}) — так "
                f"выглядит скачанная HTML-страница вместо модели. {fix}")
    if size < min_bytes:
        return (f"файл .onnx слишком мал: {size} байт "
                f"(ждём хотя бы {min_bytes // 1024 // 1024} МБ) — похоже на обрыв "
                f"закачки. {fix}")
    return None


def _digest(path: pathlib.Path) -> str:
    """sha256 файла кусками: модели весят сотни мегабайт."""
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_onnx(archive: pathlib.Path, dest: pathlib.Path) -> None:
    """Достать единственную .onnx-модель из архива релиза sherpa-onnx.

    Архив разворачивается во временную папку рядом: путь внутри задан не нами,
    и распаковывать его прямо в models/ значит согласиться на любую структуру,
    которую туда положили.
    """
    import shutil as _sh
    import tarfile
    import tempfile

    with tempfile.TemporaryDirectory(dir=str(dest.parent)) as tmp:
        with tarfile.open(archive, "r:bz2") as tar:
            members = [m for m in tar.getmembers()
                       if m.isfile() and m.name.endswith(".onnx")
                       and ".." not in m.name and not m.name.startswith("/")]
            if not members:
                raise SystemExit(f"в архиве нет .onnx: {archive.name}")
            best = max(members, key=lambda m: m.size)
            tar.extract(best, path=tmp, filter="data")
            _sh.move(str(pathlib.Path(tmp) / best.name), str(dest))


class DownloadBusy(SystemExit):
    """Цель качает другой процесс, и его файл не растёт: отказ «занято», а не порча.

    Подкласс `SystemExit`: для терминала и кнопки это такой же отказ строкой
    причины. `main` установщика движка ловит его раньше общего `SystemExit` и
    отвечает своим кодом «занято» — и за набор голосов, и за веса Nemotron."""


#: Сосед держит замок цели: опрос раз в столько секунд, пока его `.part` растёт.
LOCK_POLL_S = 2.0
#: Сколько ждать соседа, чей `.part` не растёт. Потолок по прогрессу, а не по
#: часам: 40 МБ через корпоративный прокси на 53 КБ/с — это 13 минут живой загрузки.
NEIGHBOUR_STALL_S = 300.0
_ARCHIVES = (".tar.bz2", ".tar.gz")


@contextlib.contextmanager
def _download_lock(dest: pathlib.Path, part: pathlib.Path, *, sleep=None, now=None):
    """Одна загрузка в одну цель: flock на `.<имя>.download.lock` рядом с целью.

    Писателей цели несколько — кнопка набора голосов (`--diar`), установщик
    движка (`ensure_voice_set`), человек из терминала, — и без замка второй
    принимал живой `.part` соседа за свой обрыв и дописывал в него (№643: битая
    модель на месте с кодом 0). Том без flock — загрузка без замка с
    предупреждением, как до замка: она повторяема и сверяется суммой."""
    sleep = sleep or time.sleep
    now = now or time.monotonic
    lock = dest.with_name(f".{dest.name}.download.lock")
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        print(f"замок загрузки не взять ({e}) — качаю без защиты от параллельной загрузки")
        yield
        return
    with os.fdopen(fd, "r+b") as f:
        waited = False
        seen, since = -1, now()
        while True:
            # Одна попытка: паузы — свои, по прогрессу соседа; и «ждали» верно даже
            # тогда, когда сосед закончил за долю секунды.
            outcome = file_locks.acquire_outcome(f, attempts=1, sleep=sleep)
            if outcome == file_locks.TAKEN:
                break
            if outcome == file_locks.NO_FLOCK:
                print("замок загрузки не взять: том без flock — параллельную загрузку "
                      "этой цели не отличу")
                break
            if not waited:
                # Одна строка, не на каждом опросе: кнопка читает вывод после выхода (№640).
                print(f"жду: идёт другая загрузка {dest.name}")
                waited = True
            try:
                size = part.stat().st_size
            except OSError:
                size = -1
            if size != seen:
                seen, since = size, now()
            elif now() - since >= NEIGHBOUR_STALL_S:
                raise DownloadBusy(
                    f"идёт другая загрузка {dest.name}, и её файл не растёт "
                    f"{int(NEIGHBOUR_STALL_S)} с — дождитесь её конца и повторите")
            sleep(LOCK_POLL_S)
        yield


def _in_place(dest: pathlib.Path, url: str, sha256: str) -> bool:
    """Цель уже стоит с этой суммой — её поставил сосед, пока мы шли к замку.

    Верим только сумме: без неё сосед мог поставить файл с другого адреса
    (`--url` против адреса по умолчанию), и годная форма — не наш файл (выход
    №643, DS I1). У архива сумма — архива, а не распакованной цели. Нечитаемая
    цель — «не на месте»: причину скажет публикация."""
    if not sha256 or url.endswith(_ARCHIVES) or not dest.is_file():
        return False
    try:
        return _digest(dest) == sha256
    except OSError:
        return False


def _part_source(part: pathlib.Path, url: str, sha256: str) -> None:
    """`.part` продолжаем, только если он начат с того же адреса и суммы.

    Источник пишется рядом (`.part.src`) до первого байта. Без него обрыв одной
    модели и загрузка другой в ту же цель склеивали два файла: с суммой — ложное
    «файл по этому адресу изменился», без суммы — битая модель с кодом 0 (вход
    №643, DS C1). `.part` без записи источника — тоже с нуля."""
    src = part.with_name(part.name + ".src")
    want = f"{url}\n{sha256}\n"
    try:
        same = src.read_text(encoding="utf-8") == want
    except (OSError, ValueError):   # обрезанный сайдкар не в UTF-8 — тоже чужой
        same = False
    if not same:
        part.unlink(missing_ok=True)
    src.write_text(want, encoding="utf-8")


def download(url: str, dest: pathlib.Path, expect_mb: int, onnx: bool = True,
             sha256: str = "") -> None:
    """Скачать модель. Печатает адрес ДО соединения — это единственный выход в сеть.

    `onnx=False` — для файлов-спутников вроде `tokens.txt`: они текстовые, и
    проверка на ONNX-магию отвергла бы их как «скачанную HTML-страницу».
    Всё — от чтения `.part` до публикации — под замком цели (№643).
    """
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SystemExit(f"не создать каталог модели {dest.parent}: {e}") from None
    part = dest.with_suffix(dest.suffix + ".part")
    with _download_lock(dest, part):
        # Под замком, а не только после ожидания: сосед мог закончить между
        # проверкой вызывающего и нашим flock (выход №643, S M3).
        if _in_place(dest, url, sha256):
            print(f"уже на месте: {dest} — поставила соседняя загрузка")
            return
        try:
            _part_source(part, url, sha256)
        except OSError as e:
            raise SystemExit(f"не подготовить загрузку {part}: {e}") from None
        _download_locked(url, dest, part, expect_mb, onnx, sha256)


def _download_locked(url: str, dest: pathlib.Path, part: pathlib.Path, expect_mb: int,
                     onnx: bool, sha256: str) -> None:
    """Тело загрузки: зовётся только из `download`, под замком цели."""
    import tarfile

    print(f"качаю {expect_mb} МБ:\n  {url}")
    # Зеркало рвёт длинные закачки на середине — на модели в 239 МБ приходило
    # 25 МБ без единой ошибки, и файл молча оказывался битым. Поэтому качаем с
    # докачкой: сверяем размер с Content-Length и дотягиваем хвост по Range.
    # Первая же попытка обычно и последняя; цикл нужен ровно для этого случая.
    try:
        total = 0
        for attempt in range(1, 7):
            done = part.stat().st_size if part.exists() else 0
            if total and done >= total:
                break
            req = urllib.request.Request(url)
            if done:
                req.add_header("Range", f"bytes={done}-")
                print(f"  докачиваю с {done // 1024 // 1024} МБ (попытка {attempt})")
            try:
                # nosemgrep — адрес из константы MODELS, не пользовательский ввод
                resp = urllib.request.urlopen(req, timeout=120)
            except urllib.error.HTTPError as e:
                # Хвоста нет: `.part` уже полный — сосед убит между последним
                # куском и публикацией. Зеркала отвечают на это 416, и каждый
                # повтор упирался в него же (выход №643, Haiku C1). Полноту
                # решают сумма и проверка формы при публикации.
                if done and e.code == 416:
                    break
                raise
            with resp:
                # Докачка законна только на 206: сервер, не знающий Range,
                # отдаёт 200 и файл целиком — дописывать его к хвосту значило
                # бы удвоить файл; по размеру он проходил проверку, а при
                # своём зеркале (--url, без суммы) битым и оставался
                # (аудит DeepSeek 16.08). Тогда начинаем с нуля.
                if done and resp.status != 206:
                    done = 0
                    total = 0
                if not total:
                    length = resp.headers.get("Content-Length")
                    total = int(length) + done if length else 0
                with part.open("ab" if done else "wb") as out:
                    shutil.copyfileobj(resp, out, length=1024 * 256)
            if not total:      # сервер не сказал размер — верим одной попытке
                break
        if total and part.stat().st_size < total:
            got, want = part.stat().st_size, total
            part.unlink(missing_ok=True)
            raise SystemExit(
                f"не докачалось: {got} из {want} байт за шесть попыток.\n"
                "проверьте сеть или укажите своё зеркало через --url")
    except (urllib.error.URLError, OSError) as e:
        # Недокачанное НЕ удаляем: следующий запуск продолжит с этого места.
        # Первая версия чистила .part на любой ошибке — и каждая попытка
        # начиналась с нуля, хотя сервер поддерживает Range. На модели в
        # 228 МБ по рвущемуся каналу это означало «никогда».
        done = part.stat().st_size // 1024 // 1024 if part.exists() else 0
        raise SystemExit(
            f"не скачалось: {e}\n"
            + (f"на диске уже {done} МБ — повторите ту же команду, докачается "
               "с этого места\n" if done else "")
            + "или укажите своё зеркало через --url")
    # Сумма, распаковка и публикация — той же строкой причины, что сеть: кнопка
    # показывает последнюю строку вывода, и трасса Python там — не причина (вход
    # №643, DS I4: место на диске, права, битый архив).
    # Негодный `.part` уходит вместе с источником: оставленный, он докачивался бы
    # по Range тем же негодным файлом при каждом повторе (выход №643, Haiku C1).
    src = part.with_name(part.name + ".src")
    try:
        _publish(url, dest, part, expect_mb, onnx, sha256)
    except (OSError, tarfile.TarError) as e:
        part.unlink(missing_ok=True)
        src.unlink(missing_ok=True)
        raise SystemExit(f"не удалось сохранить модель {dest.name}: {e}") from None
    except SystemExit:
        part.unlink(missing_ok=True)
        src.unlink(missing_ok=True)
        raise
    src.unlink(missing_ok=True)
    print(f"готово: {dest} ({dest.stat().st_size // 1024 // 1024} МБ)")


def _publish(url: str, dest: pathlib.Path, part: pathlib.Path, expect_mb: int,
             onnx: bool, sha256: str) -> None:
    """Сверить скачанный `.part` и положить его на место цели."""
    if sha256:
        got = _digest(part)
        if got != sha256:
            # Файл под тем же URL стал другим. Это либо подмена на зеркале,
            # либо законное обновление модели — различить их отсюда нельзя,
            # поэтому решает человек, а не скрипт (аудит 16.08).
            part.unlink(missing_ok=True)
            raise SystemExit(
                f"контрольная сумма не сошлась:\n  ждали {sha256}\n"
                f"  получили {got}\n"
                "файл по этому адресу изменился. Если это ожидаемое обновление "
                "модели — обновите sha256 в scripts/get_models.py; если нет — "
                "не ставьте его: модель слушает все ваши встречи.")

    if url.endswith(_ARCHIVES):
        _extract_onnx(part, dest)
        part.unlink(missing_ok=True)
        problem = check(dest, min_bytes=1024 * 1024)
        if problem:
            dest.unlink(missing_ok=True)
            raise SystemExit(f"распакованная модель не годится: {problem}")
    elif onnx:
        problem = check(part, min_bytes=max(1, expect_mb // 2) * 1024 * 1024)
        if problem:
            part.unlink(missing_ok=True)
            raise SystemExit(f"скачанный файл не годится: {problem}")
        part.replace(dest)
    else:
        if part.stat().st_size < 1024:
            part.unlink(missing_ok=True)
            raise SystemExit(f"скачанный файл пуст: {url}")
        part.replace(dest)


def list_models() -> None:
    print("Модели спикер-эмбеддингов для живой диаризации:\n")
    for key, m in MODELS.items():
        mark = " (по умолчанию)" if key == DEFAULT else ""
        print(f"  {key}{mark}\n    {m.size_mb} МБ · {m.note}\n    {m.url}")
    print("\nМодели сегментации речи (для полной диаризации sherpa-onnx):\n")
    for key, m in SEGMENTATION.items():
        mark = " (по умолчанию)" if key == SEG_DEFAULT else ""
        print(f"  {key}{mark}\n    {m.size_mb} МБ · {m.note}\n    {m.url}")
    print("\nМодели распознавания речи:\n")
    for key, m in STT_MODELS.items():
        mark = " (по умолчанию)" if key == STT_DEFAULT else ""
        print(f"  {key}{mark}\n    {m.size_mb} МБ · {m.note}\n    {m.url}")
    print(f"\nUpstream: {UPSTREAM}, {SEG_UPSTREAM} и {STT_UPSTREAM}. "
          "Лицензии моделей принимаете вы.")
    print("Поставить: .venv/bin/python scripts/get_models.py --diar [--model КЛЮЧ]"
          "  (эмбеддинги и сегментация; с --url или --dest — только эмбеддинги)")
    print("           .venv/bin/python scripts/get_models.py --segmentation")
    print("           .venv/bin/python scripts/get_models.py --stt sensevoice")


# Распакованная сегментация весит около шести мегабайт. Порог ниже, чем у
# эмбеддингов: тот же `check`, другая планка.
SEG_MIN_BYTES = 1024 * 1024
_DIAR_RETRY = ".venv/bin/python scripts/get_models.py --diar"
_SEG_NOT_INSTALLED = "сегментация не ставилась — --segmentation"
_SEG_NOT_CHECKED = "сегментация не проверялась — --check --segmentation"
_LIVE_ON = ("живая диаризация включится при следующем старте встречи "
            "(sufler.live_diarize уже true по умолчанию)")


def _exit_text(exc: SystemExit) -> str:
    """Текст SystemExit одной строкой: приложение показывает только последнюю."""
    code = exc.code
    if code is None or isinstance(code, int):
        text = "" if code is None else str(code)
    else:
        text = str(code)
    return " ".join(text.split())


def _custom_target(args: argparse.Namespace) -> bool:
    """Свой адрес или свой путь — одна цель, не набор."""
    return args.url is not None or args.dest is not None


def _chosen_targets(args: argparse.Namespace) -> list[str]:
    chosen = []
    if args.stt:
        chosen.append("--stt")
    if args.segmentation:
        chosen.append("--segmentation")
    if args.diar:
        chosen.append("--diar")
    return chosen


def _install_stt(args: argparse.Namespace) -> int:
    """Модель распознавания. В `--check` — 1, если файла не хватает, и без выхода."""
    stt_dest = args.dest or stt_target()
    tokens = stt_dest.with_name("tokens.txt")
    # Модель без словаря не работает, поэтому «на месте» — это оба файла.
    stt_problem = check(stt_dest, min_bytes=100 * 1024 * 1024)
    if not stt_problem and not tokens.exists():
        stt_problem = f"нет словаря токенов рядом с моделью: {tokens}"
    if args.check:
        print(stt_problem or f"модель распознавания на месте: {stt_dest}")
        return 1 if stt_problem else 0
    if stt_problem:
        print(stt_problem.split(" — ")[0])
        stt = STT_MODELS[args.stt]
        download(args.url or stt.url, stt_dest, stt.size_mb,
                 sha256="" if args.url else stt.sha256)
        download(f"{STT_MIRROR}/tokens.txt", tokens, 1, onnx=False,
                 sha256=TOKENS_SHA256)
        print("включить: stt.backend: sensevoice в config/config.yaml")
    else:
        print(f"модель распознавания уже стоит: {stt_dest}")
    return 0


def _install_segmentation(args: argparse.Namespace) -> int:
    """Только `--segmentation`: свой `--url`/`--dest` или стандартное место."""
    seg_dest = args.dest or seg_target()
    seg_problem = check(seg_dest, min_bytes=SEG_MIN_BYTES)
    if args.check:
        print(seg_problem or f"модель сегментации на месте: {seg_dest}")
        return 1 if seg_problem else 0
    if seg_problem:
        print(seg_problem.split(" — ")[0])
        seg = SEGMENTATION[SEG_DEFAULT]
        download(args.url or seg.url, seg_dest, seg.size_mb,
                 sha256="" if args.url else seg.sha256)
    else:
        print(f"модель сегментации уже стоит: {seg_dest}")
    return 0


def _install_embeddings(args: argparse.Namespace) -> int:
    """`--diar` с `--url` или `--dest`: одна цель, эмбеддинги, как раньше."""
    dest = args.dest or diar_target()
    problem = check(dest)
    if args.check:
        print(problem or f"модель на месте: {dest}")
        print(_SEG_NOT_CHECKED)
        return 1 if problem else 0
    if not problem:
        print(f"модель уже стоит: {dest} — нечего делать")
    else:
        print(problem.split(" — ")[0])
        model = MODELS[args.model]
        download(args.url or model.url, dest, model.size_mb,
                 sha256="" if args.url else model.sha256)
        print(_LIVE_ON)
    print(_SEG_NOT_INSTALLED)
    return 0


def _install_diar_bundle(args: argparse.Namespace) -> int:
    """`--diar` без своего адреса и пути: эмбеддинги, затем сегментация.

    Сегментация одна, даже если рядом стоит `--segmentation`. `SystemExit`
    загрузки ловится только здесь, дважды: отказ эмбеддингов — сегментацию не
    качаем (без эмбеддингов она разбору не поможет, повтор поставит обе); отказ
    сегментации эмбеддинги не откатывает. В обоих случаях последняя строка —
    состояние, причина и рецепт, код 1.
    """
    emb = diar_target()
    seg_dest = seg_target()
    emb_problem = check(emb)
    seg_problem = check(seg_dest, min_bytes=SEG_MIN_BYTES)
    if args.check:
        print(emb_problem or f"модель на месте: {emb}")
        print(seg_problem or f"модель сегментации на месте: {seg_dest}")
        return 1 if emb_problem or seg_problem else 0
    if not emb_problem and not seg_problem:
        print(f"модель уже стоит: {emb} и {seg_dest} — нечего делать")
        return 0
    if emb_problem:
        print(emb_problem.split(" — ")[0])
        model = MODELS[args.model]
        try:
            download(model.url, emb, model.size_mb, sha256=model.sha256)
        except SystemExit as exc:
            print(f"эмбеддинги не поставлены: {_exit_text(exc)} — повторить: {_DIAR_RETRY}")
            return 1
        print(_LIVE_ON)
    if seg_problem:
        print(seg_problem.split(" — ")[0])
        seg = SEGMENTATION[SEG_DEFAULT]
        try:
            download(seg.url, seg_dest, seg.size_mb, sha256=seg.sha256)
        except SystemExit as exc:
            reason = _exit_text(exc)
            print(f"эмбеддинги стоят, сегментации нет: {reason} — повторить: {_DIAR_RETRY}")
            return 1
    else:
        print(f"модель сегментации уже стоит: {seg_dest}")
    return 0


def main() -> int:
    # Адрес печатается до соединения — и в канале (`| tee`, лог) обязан дойти до
    # читателя тогда же, а не блоком в конце: строчная буферизация stdout (№481,
    # тот же довод, что у install_engine.main).
    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--diar", action="store_true",
                    help="эмбеддинги и сегментация (с --url или --dest — только эмбеддинги)")
    ap.add_argument("--segmentation", action="store_true",
                    help="модель сегментации речи (нужна полной диаризации sherpa-onnx)")
    ap.add_argument("--stt", nargs="?", const=STT_DEFAULT, choices=sorted(STT_MODELS),
                    help="модель распознавания речи (sensevoice — китайский и ещё 4 языка)")
    ap.add_argument("--model", default=DEFAULT, choices=sorted(MODELS),
                    help=f"какую модель брать (по умолчанию {DEFAULT})")
    ap.add_argument("--url", default=None, help="своя ссылка на .onnx вместо известных")
    ap.add_argument("--dest", type=pathlib.Path, default=None,
                    help="куда положить (по умолчанию models/diar/embedding.onnx)")
    ap.add_argument("--check", action="store_true",
                    help="только проверить, что выбранные модели на месте (без сети)")
    ap.add_argument("--list", action="store_true", help="показать модели и выйти")
    args = ap.parse_args()

    if args.list:
        list_models()
        return 0
    if not args.diar and not args.segmentation and not args.stt:
        ap.print_help()
        return 0

    # Один адрес и один путь на несколько моделей положили бы один файл во все места.
    chosen = _chosen_targets(args)
    if _custom_target(args) and len(chosen) > 1:
        ap.error("один --url или --dest нельзя делить между несколькими моделями "
                 "(--stt, --segmentation, --diar): один файл лёг бы во все места")

    # `--diar` без своего адреса и пути — набор из двух файлов. С `--segmentation`
    # сегментация всё равно ставится и проверяется один раз.
    bundle = bool(args.diar and not _custom_target(args))
    rc_stt = _install_stt(args) if args.stt else 0
    if bundle:
        rc_diar = _install_diar_bundle(args)
    elif args.diar:
        rc_diar = _install_embeddings(args)
    else:
        rc_diar = 0
    rc_seg = _install_segmentation(args) if args.segmentation and not bundle else 0
    if args.check:
        return 1 if rc_stt or rc_diar or rc_seg else 0
    return 1 if rc_diar else 0


if __name__ == "__main__":
    sys.exit(main())
