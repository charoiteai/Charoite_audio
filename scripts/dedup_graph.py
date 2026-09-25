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

Второе правило — конфликтные копии «Имя 2.md … Имя 12.md» рядом с «Имя.md» в
папках архива и в «Документации» (№361). Их порождала перезапись документов
встречи на месте при каждом проходе архиватора: в iCloud такая запись давала
копию, и 23.09 их было 7594. Жёсткая ссылка их не лечит: путь остаётся, и
вкладка «Задачи» читает каждое поручение столько раз, сколько у него копий.
Поэтому побайтно равная копия уезжает из графа в резерв
(`charoite_paths.graph_backups(граф, "dedup_copies")`, вне iCloud) со строкой
в манифесте. Отличающаяся — только в отчёт, решает человек.

Два правила — два разрешения: `--apply` и `sufler.dedup_files` связывают
ссылками, `--apply-copies` и `sufler.dedup_copies` убирают копии. Одно не
включает другое (Critical Opus входного круга №361).

Запуск: python3 scripts/dedup_graph.py [--apply] [--apply-copies] [--graph ПУТЬ]
Без ключей только показывает, что будет сделано.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import shutil
import sys
import time

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import charoite_paths  # noqa: E402
import graphs  # noqa: E402
import meeting_archive  # noqa: E402
from charoite_paths import resolve_root  # noqa: E402

ARCHIVE_DIR = "Встречи-архив"
# Файлы мельче этого дедуплицировать бессмысленно: выигрыш меньше, чем риск
# запутать человека жёсткими ссылками на мелочь.
MIN_SIZE = 4096


def _root() -> pathlib.Path:
    """Корень данных — спрашиваем канон на вызове, а не запоминаем на импорте."""
    return resolve_root(__file__)


def _cfg() -> dict:
    cfg_path = _root() / "config" / "config.yaml"
    if not cfg_path.exists():
        return {}
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}


def _allowed_by_config(key: str = "dedup_files") -> bool:
    """Строго is True: «false», пустое значение и мусор разрешением не считаются.
    У каждого правила свой ключ: `dedup_files` — ссылки, `dedup_copies` — копии."""
    return (_cfg().get("sufler") or {}).get(key) is True


def graph_dir(explicit: str | None) -> pathlib.Path | None:
    """Явный аргумент — как есть (относительный от корня данных); иначе —
    единая точка src/graphs.py (SUFLER_GRAPH_DIR → config.yaml)."""
    if explicit:
        return graphs.resolve(explicit)
    if not (_root() / "config" / "config.yaml").exists():
        return None
    return graphs.graph_dir()


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


def link_copy(original: pathlib.Path, copy: pathlib.Path,
              expected: str | None = None) -> str:
    """Заменить копию жёсткой ссылкой. Возвращает статус для отчёта.

    `expected` — хэш, с которым копия попала в группу. Хэширование графа
    идёт минуты, а связывание — потом: если между ними живой конвейер
    переписал копию (разбор долгой встречи, retro_fill, ручная правка),
    подмена уничтожала НОВОЕ содержимое старым inode, и следа не
    оставалось — бэкапа эта ветка не делает (аудит ночи 26.08, GLM
    Critical 2 + DS Minor 10). Перед подменой сверяем ещё раз.
    """
    if expected is not None:
        try:
            if digest(copy) != expected or digest(original) != expected:
                return "изменился во время прогона — пропуск"
        except OSError as e:
            return f"перепроверка не удалась ({e.strerror})"
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


COPIES_KIND = "dedup_copies"


def park_copy(graph: pathlib.Path, copy: meeting_archive.ConflictCopy, dest: pathlib.Path,
              manifest) -> str:
    """Убрать одну побайтно равную копию из графа в резерв. Статус — для отчёта.

    В резерв всегда идёт НЕЗАВИСИМАЯ копия (`copy2`), а не `rename`: у
    жёстко связанной копии перенос сохранил бы inode, и правка живого
    оригинала меняла бы «резерв» (Important Opus входного круга №361, тот же
    урок, что со снимками 16.08). Резерв сверяется с прочитанными байтами до
    удаления: у файла, выгруженного iCloud с диска, в резерв могла уехать
    пустышка. Строка манифеста пишется раньше удаления: оборванный прогон
    оставляет след всего, что уже убрано.
    """
    def identity(p: pathlib.Path) -> tuple[int, int, int]:
        st = os.lstat(p)
        return (st.st_ino, st.st_size, st.st_mtime_ns)

    try:
        before = identity(copy.copy)
        data = copy.copy.read_bytes()
        if data != copy.original.read_bytes():
            return "изменился во время прогона — пропуск"
        target = dest / copy.copy.relative_to(graph)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        shutil.copy2(copy.copy, part)
        # iCloud может подменить файл, пока мы его копируем (докачка серверной
        # версии): удалили бы версию, которой нет в резерве (Important Opus
        # круга 2 по №361). Файл сверяем дважды — до записи резерва и прямо
        # перед удалением; окно сжато до одного вызова, но не до нуля.
        if part.read_bytes() != data or identity(copy.copy) != before:
            part.unlink(missing_ok=True)
            return "изменился во время прогона — пропуск"
        part.replace(target)
        rel = copy.copy.relative_to(graph)
        manifest.write(f"{rel}\t{copy.original.relative_to(graph)}\t"
                       f"{len(data)}\t{hashlib.sha256(data).hexdigest()}\n")
        manifest.flush()
        if identity(copy.copy) != before:
            manifest.write(f"# оставлена: {rel} — изменилась во время переноса, резерв выше лишний\n")
            manifest.flush()
            return "изменился во время переноса — копия оставлена"
        copy.copy.unlink()
        return "ok"
    except OSError as e:
        return f"не удалось ({e.strerror})"


def park_copies(graph: pathlib.Path, apply: bool) -> None:
    """Второе правило: конфликтные копии «Имя N» документов встреч (№361)."""
    found = meeting_archive.conflict_copies(graph)
    if not found:
        print("конфликтных копий «Имя N» нет")
        return
    same = [c for c in found if c.same is True]
    other = [c for c in found if c.same is False]
    unread = [c for c in found if c.same is None]
    size = 0
    for c in same:
        try:
            size += c.copy.stat().st_size
        except OSError:
            continue
    if not apply:
        print(f"конфликтных копий «Имя N»: {len(found)}; побайтно равны оригиналу — {len(same)} "
              f"({size / 1024 / 1024:.1f} МБ), будут убраны с --apply-copies "
              f"(sufler.dedup_copies выключен); отличаются — {len(other)}, только отчёт")
    elif not same:
        # Ключ включён, а равных нет — штатная ночь после чистки №361. Строка
        # «ключ выключен» здесь лгала бы владельцу каждую ночь.
        print(f"конфликтных копий «Имя N»: {len(found)}; побайтно равных оригиналу нет — "
              f"убирать нечего; отличаются — {len(other)}, только отчёт")
    else:
        dest = charoite_paths.secure_dir(
            charoite_paths.graph_backups(graph, COPIES_KIND, root=_root())
            / time.strftime("%Y%m%d-%H%M%S"))
        moved = 0
        failures: list[str] = []
        with (dest / "manifest.tsv").open("a", encoding="utf-8") as manifest:
            manifest.write("копия\tоригинал\tбайт\tsha256\n")
            for c in same:
                status = park_copy(graph, c, dest, manifest)
                if status == "ok":
                    moved += 1
                else:
                    failures.append(f"{c.copy.relative_to(graph)}: {status}")
        print(f"⚠️ убрано конфликтных копий «Имя N»: {moved} из {len(same)} "
              f"({size / 1024 / 1024:.1f} МБ), резерв и манифест: {dest}; "
              f"отличаются и оставлены: {len(other)}")
        if failures:
            print(f"не удалось ({len(failures)}):")
            for f in failures[:5]:
                print(f"  {f}")
    for c in other[:5]:
        print(f"  отличается от оригинала: {c.copy.relative_to(graph)}")
    if unread:
        print(f"⚠️ не прочитаны ({len(unread)}) — не трогаем:")
        for c in unread[:5]:
            print(f"  {c.copy.relative_to(graph)}")


def link_duplicates(graph: pathlib.Path, apply: bool) -> None:
    """Первое правило: побайтные копии от 4 КБ — жёсткой ссылкой на оригинал."""
    by_hash: dict[str, list[pathlib.Path]] = {}
    for p in graph.rglob("*.md"):
        # Скрытые каталоги — снимки (.cloud_backup, .tier3_backup,
        # .forget_backup, .obsidian): rglob, в отличие от шелл-глоба, в них
        # заходит, и побайтовая копия из снимка попадала в одну группу с живым
        # файлом → жёсткая ссылка на тот же inode → правка живого файла
        # меняет «бэкап», откат из него становится пустым (аудит DeepSeek 16.08).
        if any(part.startswith(".") for part in p.relative_to(graph).parts):
            continue
        try:
            if p.is_symlink() or p.stat().st_size < MIN_SIZE:
                continue
            by_hash.setdefault(digest(p), []).append(p)
        except OSError:
            continue

    groups = {h: v for h, v in by_hash.items() if len(v) > 1}
    if not groups:
        print("дублей нет")
        return

    freed = 0
    linked = 0
    already = 0
    failures: list[str] = []
    for group_hash, paths in groups.items():
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
            status = link_copy(original, copy, expected=group_hash)
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Дедупликация файлов графа: копии «Имя N» и жёсткие ссылки")
    ap.add_argument("--graph", help="путь к графу (по умолчанию sufler.graph_dir)")
    ap.add_argument("--apply", action="store_true",
                    help="связать побайтные копии ссылками (без ключа — sufler.dedup_files из конфига)")
    ap.add_argument("--apply-copies", action="store_true",
                    help="убрать конфликтные копии «Имя N» в резерв (без ключа — sufler.dedup_copies)")
    ap.add_argument("--all-graphs", action="store_true",
                    help="все графы vault с папкой «Ядра» — тот же перечень, что у graph_doctor")
    args = ap.parse_args()

    # Право на правку графа берётся из конфига, а не из строки запуска.
    # Ночная джоба не решает за человека: то же правило, что у слияния ядер
    # в tier3, и оно закреплено тестом. Оба ключа по умолчанию выключены.
    # Жёсткая ссылка безвредна для содержимого, но неожиданна для того, кто
    # правит архивную копию, считая её независимой; перенос копии убирает путь
    # из графа, хоть и в резерв.
    apply_links = args.apply or _allowed_by_config("dedup_files")
    apply_copies = args.apply_copies or _allowed_by_config("dedup_copies")

    # Перечень графов — тот же, что у доктора: сигнал о копиях горит по каждому
    # графу, и уборке нельзя видеть только основной (Important Opus круга 2 по №361).
    found = graphs.all_graphs("Ядра") if args.all_graphs else [graph_dir(args.graph)]
    found = [g for g in found if g and g.is_dir()]
    if not found:
        print("граф не найден — пропуск")
        return 0

    for graph in found:
        if len(found) > 1:
            print(f"— {graph.name}")
        # Копии — первыми: после их уборки отчёт о ссылках не считает их дважды.
        park_copies(graph, apply_copies)
        link_duplicates(graph, apply_links)
    return 0


if __name__ == "__main__":
    sys.exit(main())
