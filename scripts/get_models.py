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
import pathlib
import shutil
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

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


MODELS = {
    "eres2net-base": Model(
        url=f"{MIRROR}/3dspeaker_speech_eres2net_base_200k_sv_zh-cn_16k-common.onnx",
        size_mb=40,
        note="дефолт: ERes2Net base, обучена на 200k говорящих — ровнее всего "
             "держит смешанные встречи",
    ),
    "eres2net-en": Model(
        url=f"{MIRROR}/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx",
        size_mb=27,
        note="легче и быстрее, VoxCeleb (английский корпус); брать, если "
             "мало памяти или встречи в основном англоязычные",
    ),
    "eres2netv2": Model(
        url=f"{MIRROR}/3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx",
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
        size_mb=7,
        note="pyannote segmentation 3.0 в экспорте sherpa-onnx: границы речи и "
             "перекрытия говорящих",
        source=SEG_UPSTREAM,
    ),
    "pyannote-3.0-int8": Model(
        url=f"{SEG_MIRROR}/sherpa-onnx-pyannote-segmentation-3-0-int8.tar.bz2",
        size_mb=3,
        note="та же модель квантованная — вчетверо меньше, для слабых машин",
        source=SEG_UPSTREAM,
    ),
}
SEG_DEFAULT = "pyannote-3.0"


def seg_target(root: pathlib.Path = ROOT) -> pathlib.Path:
    """Куда кладём модель сегментации."""
    return root / "models" / "diar" / "segmentation.onnx"


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


def download(url: str, dest: pathlib.Path, expect_mb: int) -> None:
    """Скачать модель. Печатает адрес ДО соединения — это единственный выход в сеть."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    print(f"качаю {expect_mb} МБ:\n  {url}")
    try:
        # nosemgrep — адрес из константы MODELS, не пользовательский ввод
        with urllib.request.urlopen(url, timeout=120) as resp, part.open("wb") as out:
            shutil.copyfileobj(resp, out, length=1024 * 256)
    except (urllib.error.URLError, OSError) as e:
        part.unlink(missing_ok=True)
        raise SystemExit(f"не скачалось: {e}\nповторите позже или укажите свой --url")
    if url.endswith((".tar.bz2", ".tar.gz")):
        _extract_onnx(part, dest)
        part.unlink(missing_ok=True)
        problem = check(dest, min_bytes=1024 * 1024)
        if problem:
            dest.unlink(missing_ok=True)
            raise SystemExit(f"распакованная модель не годится: {problem}")
    else:
        problem = check(part, min_bytes=max(1, expect_mb // 2) * 1024 * 1024)
        if problem:
            part.unlink(missing_ok=True)
            raise SystemExit(f"скачанный файл не годится: {problem}")
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
    print(f"\nUpstream: {UPSTREAM} и {SEG_UPSTREAM}. Лицензии моделей принимаете вы.")
    print("Поставить: .venv/bin/python scripts/get_models.py --diar [--model КЛЮЧ]")
    print("           .venv/bin/python scripts/get_models.py --segmentation")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--diar", action="store_true", help="модель живой диаризации")
    ap.add_argument("--segmentation", action="store_true",
                    help="модель сегментации речи (нужна полной диаризации sherpa-onnx)")
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
    if not args.diar and not args.segmentation:
        ap.print_help()
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
            download(args.url or seg.url, seg_dest, seg.size_mb)
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
    download(args.url or model.url, dest, model.size_mb)
    print("живая диаризация включится при следующем старте встречи "
          "(sufler.live_diarize уже true по умолчанию)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
