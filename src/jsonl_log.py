"""Дописываемый журнал JSON-строк в `logs/`: одна запись — одна строка.

Писатель и читатель в одном месте (№807): их держал бенч памяти
(`scripts/memory_bench.py`), а журнал вызовов модели понадобился второй копией —
вторая копия починку оборванной строки бы не унесла (входной круг №807, DS I2).

Запись — одна `os.write` в файл, открытый с `O_APPEND`: строка короче
страницы ложится целиком и не перемешивается с соседним процессом (на APFS и
ext4 на практике, не по букве POSIX). Оборванная прошлая запись (процесс
умер, диск кончился) не склеивается с новой: перед строкой встаёт перевод,
иначе читатель выбросил бы обе. Права файла — 0600 при создании, а не по
umask: `harden_existing` зовётся только на старте.
"""
from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Callable


def append_record(path: pathlib.Path, rec: dict, *, mode: int = 0o600) -> None:
    """Дописать запись строкой. Ошибки файловой системы — наружу (OSError):
    решает вызывающий, глотать их или нет."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, mode)   # RDWR: pread последнего байта
    try:
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            line = "\n" + line
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def read_records(path: pathlib.Path, warn: Callable[[str], object] = print) -> list[dict]:
    """Строки журнала; битая строка (оборванная запись) пропускается с предупреждением."""
    path = pathlib.Path(path)
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            warn(f"⚠ {path.name}:{i}: битая строка пропущена")
    return out
