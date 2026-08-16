#!/usr/bin/env python3
"""Модель диаризации одной командой — вместо «найдите и экспортируйте ONNX».

`README` обещает метки «Собеседник 1/2/…» по голосам, а демон включает трекер
только при наличии `models/diar/embedding.onnx`. Файл в поставку не входит:
STT-модель тянется сама при первом запуске, а эмбеддер до сих пор предлагалось
найти в исследовательском репозитории 3D-Speaker и экспортировать в ONNX
самостоятельно. То есть ключевая фича продукта у нового пользователя просто не
работала, и починить это можно было только чтением документации.

    .venv/bin/python scripts/get_models.py --list             # что есть и откуда
    .venv/bin/python scripts/get_models.py --diar             # поставить дефолтную
    .venv/bin/python scripts/get_models.py --diar --model eres2net-en
    .venv/bin/python scripts/get_models.py --diar --check     # проверить, что стоит

О сети начистоту. Это единственное место в продукте, кроме опционального
облачного слоя, которое ходит наружу, — и оно ходит:

    * только когда его запустили руками (демон и приложение его не зовут);
    * на адрес, который печатается ДО скачивания;
    * один раз: дальше модель лежит файлом и работает офлайн.

`--check` не открывает соединений вовсе. Обещание «никаких сетевых вызовов,
кроме вашего localhost» в PRIVACY.md относится к работе продукта; разовая
загрузка модели описана там же отдельным пунктом — как и загрузка STT.

Лицензия моделей — на стороне upstream (3D-Speaker, Apache-2.0; зеркала ONNX
собраны для sherpa-onnx). Скрипт печатает источник, принимает условия
пользователь.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import pathlib
import shutil
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or
                    pathlib.Path(__file__).resolve().parent.parent).expanduser()

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


def seg_target(root: pathlib.Path = ROOT) -> pathlib.Path:
    """Куда кладём модель сегментации."""
    return root / "models" / "diar" / "segmentation.onnx"


def stt_target(root: pathlib.Path = ROOT) -> pathlib.Path:
    """Куда кладём модель распознавания (рядом ляжет tokens.txt)."""
    return root / "models" / "stt" / "sensevoice.onnx"


def diar_target(root: pathlib.Path = ROOT) -> pathlib.Path:
    """Путь, по которому модель ищет демон (src/daemon.py)."""
    return root / "models" / "diar" / "embedding.onnx"


def check(path: pathlib.Path, min_bytes: int = MIN_BYTES) -> str | None:
    """Что не так с этим файлом. None — модель на месте и похожа на себя.

    Проверка нарочно дешёвая и офлайновая: существование, размер, ONNX-магия.
    Загружать модель в sherpa-onnx здесь не нужно — это делает демон, а
    ответ «подойдёт ли файл» человек должен получать мгновенно и без сети.
    """
    fix = ("поставить: .venv/bin/python scripts/get_models.py --diar "
           "(модели: --list)")
    if not path.exists():
        return f"модели диаризации нет ({path}) — {fix}"
    # Сначала «это вообще ONNX?», потом «целиком ли скачалось»: HTML-страница
    # логина весит килобайты, и жаловаться на её размер — путать причину.
    with path.open("rb") as f:
        head = f.read(1)
    if head != ONNX_MAGIC:
        return (f"{path.name} не похож на .onnx (первый байт {head!r}) — так "
                f"выглядит скачанная HTML-страница вместо модели. {fix}")
    size = path.stat().st_size
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


def download(url: str, dest: pathlib.Path, expect_mb: int, onnx: bool = True,
             sha256: str = "") -> None:
    """Скачать модель. Печатает адрес ДО соединения — это единственный выход в сеть.

    `onnx=False` — для файлов-спутников вроде `tokens.txt`: они текстовые, и
    проверка на ONNX-магию отвергла бы их как «скачанную HTML-страницу».
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
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
            # nosemgrep — адрес из константы MODELS, не пользовательский ввод
            with urllib.request.urlopen(req, timeout=120) as resp:
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

    if url.endswith((".tar.bz2", ".tar.gz")):
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
    print(f"готово: {dest} ({dest.stat().st_size // 1024 // 1024} МБ)")


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
    print("Поставить: .venv/bin/python scripts/get_models.py --diar [--model КЛЮЧ]")
    print("           .venv/bin/python scripts/get_models.py --segmentation")
    print("           .venv/bin/python scripts/get_models.py --stt sensevoice")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--diar", action="store_true", help="модель живой диаризации")
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
                    help="только проверить, что модель на месте (без сети)")
    ap.add_argument("--list", action="store_true", help="показать модели и выйти")
    args = ap.parse_args()

    if args.list:
        list_models()
        return 0
    if not args.diar and not args.segmentation and not args.stt:
        ap.print_help()
        return 0

    if args.stt:
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
        if not args.diar and not args.segmentation:
            return 0

    if args.segmentation:
        seg_dest = args.dest or seg_target()
        seg_min = 1024 * 1024   # распакованная сегментация весит около шести
        seg_problem = check(seg_dest, min_bytes=seg_min)
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
        if not args.diar:
            return 0

    dest = args.dest or diar_target()
    problem = check(dest)
    if args.check:
        print(problem or f"модель на месте: {dest}")
        return 1 if problem else 0
    if not problem:
        print(f"модель уже стоит: {dest} — нечего делать")
        return 0

    print(problem.split(" — ")[0])
    model = MODELS[args.model]
    download(args.url or model.url, dest, model.size_mb,
             sha256="" if args.url else model.sha256)
    print("живая диаризация включится при следующем старте встречи "
          "(sufler.live_diarize уже true по умолчанию)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
