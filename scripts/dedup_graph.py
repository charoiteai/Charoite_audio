#!/usr/bin/env python3
"""Ночная дедупликация файлов графа.

Не путать с tier3: тот сшивает СМЫСЛОВЫЕ дубли ядер («Настройка доступа к API»
и «Получение токена» — одна тема разными словами) через bge-m3 и NLI. Здесь
задача проще и грубее: побайтовые копии одного файла в разных папках.

Откуда они берутся. Конвейер намеренно кладёт документы встречи дважды:
оригинал в «Документация/Стенограммы встреч», копию — в
«Встречи-архив/<дата — название>», чтобы папку встречи можно было открыть из
Finder. Для человека это удобно и остаётся как есть. Но копии растут: на
рабочем графе 214 групп и 37% объёма — а это лишняя синхронизация iCloud,
лишний вес на iPhone и двойная работа при индексации.

Что делает скрипт: заменяет копию жёсткой ссылкой на оригинал. Файл остаётся
на месте и открывается отовсюду, но занимает место один раз; правка через
любой путь видна везде. Если файловая система ссылку не даёт (так бывает в
синхронизируемых папках), копия остаётся нетронутой — тогда это просто отчёт.

Оригиналом считается файл ВНЕ «Встречи-архив»: архив производен по смыслу.

Запуск: python3 scripts/dedup_graph.py [--apply] [--graph ПУТЬ]
Без --apply только показывает, что будет сделано.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import sys

import yaml

ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or
                    pathlib.Path(__file__).resolve().parent.parent).expanduser()
ARCHIVE_DIR = "Встречи-архив"
# Файлы мельче этого дедуплицировать бессмысленно: выигрыш меньше, чем риск
# запутать человека жёсткими ссылками на мелочь.
MIN_SIZE = 4096


def _cfg() -> dict:
    cfg_path = ROOT / "config" / "config.yaml"
    if not cfg_path.exists():
        return {}
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


def _allowed_by_config() -> bool:
    """Строго is True: «false», пустое значение и мусор разрешением не считаются."""
    return (_cfg().get("sufler") or {}).get("dedup_files") is True


def graph_dir(explicit: str | None) -> pathlib.Path | None:
    if explicit:
        return pathlib.Path(explicit).expanduser()
    cfg_path = ROOT / "config" / "config.yaml"
    if not cfg_path.exists():
        return None
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    raw = str((cfg.get("sufler") or {}).get("graph_dir", "")).strip()
    return pathlib.Path(raw).expanduser() if raw else None


def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def same_file(a: pathlib.Path, b: pathlib.Path) -> bool:
    """Уже одна и та же inode — работа сделана в прошлый раз."""
    try:
        sa, sb = a.stat(), b.stat()
        return sa.st_ino == sb.st_ino and sa.st_dev == sb.st_dev
    except OSError:
        return False


def pick_original(paths: list[pathlib.Path]) -> pathlib.Path:
    """Оригинал — не из архива; при прочих равных тот, что старше."""
    outside = [p for p in paths if ARCHIVE_DIR not in p.parts]
    pool = outside or paths
    return min(pool, key=lambda p: (p.stat().st_mtime, str(p)))


def link_copy(original: pathlib.Path, copy: pathlib.Path) -> str:
    """Заменить копию жёсткой ссылкой. Возвращает статус для отчёта."""
    tmp = copy.with_name(copy.name + ".dedup-tmp")
    try:
        os.link(original, tmp)
    except OSError as e:
        return f"ссылка не поддерживается ({e.strerror})"
    try:
        tmp.replace(copy)          # атомарная подмена: файл не исчезает ни на миг
        return "ok"
    except OSError as e:
        tmp.unlink(missing_ok=True)
        return f"подмена не удалась ({e.strerror})"


def main() -> int:
    ap = argparse.ArgumentParser(description="Дедупликация файлов графа жёсткими ссылками")
    ap.add_argument("--graph", help="путь к графу (по умолчанию sufler.graph_dir)")
    ap.add_argument("--apply", action="store_true",
                    help="связать копии (без ключа берётся sufler.dedup_files из конфига)")
    args = ap.parse_args()

    # Право на правку графа берётся из конфига, а не из строки запуска.
    # Ночная джоба не решает за человека: то же правило, что у слияния ядер
    # в tier3, и оно закреплено тестом. Ключ по умолчанию выключен —
    # жёсткая ссылка безвредна для содержимого, но неожиданна для того, кто
    # правит архивную копию, считая её независимой.
    apply = args.apply or _allowed_by_config()

    graph = graph_dir(args.graph)
    if not graph or not graph.is_dir():
        print("граф не найден — пропуск")
        return 0

    by_hash: dict[str, list[pathlib.Path]] = {}
    for p in graph.rglob("*.md"):
        try:
            if p.is_symlink() or p.stat().st_size < MIN_SIZE:
                continue
            by_hash.setdefault(digest(p), []).append(p)
        except OSError:
            continue

    groups = {h: v for h, v in by_hash.items() if len(v) > 1}
    if not groups:
        print("дублей нет")
        return 0

    freed = 0
    linked = 0
    already = 0
    failures: list[str] = []
    for paths in groups.values():
        original = pick_original(paths)
        for copy in paths:
            if copy == original:
                continue
            if same_file(original, copy):
                already += 1
                continue
            size = copy.stat().st_size
            if not apply:
                freed += size
                linked += 1
                continue
            status = link_copy(original, copy)
            if status == "ok":
                freed += size
                linked += 1
            else:
                failures.append(f"{copy.relative_to(graph)}: {status}")

    mode = "связано" if apply else "будет связано (sufler.dedup_files выключен)"
    print(f"групп дублей: {len(groups)}, {mode}: {linked}, "
          f"освобождается: {freed / 1024 / 1024:.1f} МБ"
          + (f", уже связано ранее: {already}" if already else ""))
    if failures:
        print(f"не удалось ({len(failures)}) — копии оставлены как есть:")
        for f in failures[:5]:
            print(f"  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
