#!/usr/bin/env python3
"""Разовая правка формата поручений в уже накопленных минутках.

Новые встречи нормализуются на выходе демона, но у пользователя за месяцы
работы лежат десятки файлов, где поручения записаны прозой модели и потому
невидимы окну «Задачи». Замер по рабочему графу: 89 файлов минуток, задач
видно 4, после правки — 275.

Правится только раздел поручений и только формат: текст, имена и сроки
остаются как были. Статус, поставленный человеком или контролем задач
(выполнено, снято «[-]», возвращено, свой символ), не трогается (task_line,
№366): файл, где преобразование изменило бы хоть один статус, не пишется и
называется в отчёте с номером строки. По умолчанию — сухой прогон; запись — под
общим замком графа и с корнем данных из CHAROITE_ROOT, как у остальных пишущих
в граф. Оригинал каждого переписанного файла и манифест (путь, sha256 до и
после) ложатся в копии графа вне синхронизируемой папки. Код выхода 1 — что-то
осталось нетронутым: не прочитан, статус, чужая запись посреди правки.

    python3 scripts/fix_action_items.py                 # показать, что изменится
    python3 scripts/fix_action_items.py --apply         # применить
    python3 scripts/fix_action_items.py --graph ПУТЬ    # другой граф
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import pathlib
import re
import sys
import time


# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py. Вставка —
# только чтобы импортировать сам канон.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import charoite_paths  # noqa: E402
import file_locks  # noqa: E402
import graphs  # noqa: E402
import safe_write  # noqa: E402
import task_line  # noqa: E402
from action_items import normalize  # noqa: E402
from charoite_paths import RootNotNamed, require_data_root, resolve_root  # noqa: E402

LOCK_WAIT = 5 * 60      # общий замок пишущих в граф: дольше держит только зависший сосед
# Оригиналы переписанных минуток — в копиях графа рядом с прочими уборками (dedup_graph):
# разовая правка сотен файлов синхронизируемого графа обязана быть обратимой (Opus,
# критика 1 круга 1 по коду №366).
COPIES_KIND = "fix_action_items"

# задачи, видимые во вкладке, — открытые и выполненные; снятое контролем «[-]» вкладка не
# показывает, и normalize его не трогает (task_line, №366)
CHECKBOX = re.compile(r"^\s*[-*] \[[ xX]\] ", re.M)


def graph_dir(explicit: str | None) -> pathlib.Path | None:
    """Явный аргумент — как есть (относительный от корня данных); иначе —
    единая точка src/graphs.py (SUFLER_GRAPH_DIR → config.yaml)."""
    if explicit:
        return graphs.resolve(explicit)
    if not (resolve_root(__file__) / "config" / "config.yaml").exists():
        return None
    return graphs.graph_dir()


def _graph_lock(graph: pathlib.Path, root: pathlib.Path):
    """Общий замок пишущих в граф (file_locks): разбор встречи, облачная ревизия,
    ночь и контроль задач пишут те же файлы минуток. Без него запись шла
    «tmp + replace» поверх чужой правки, сделанной между чтением и записью
    (Opus C3 входного круга №366)."""
    try:
        lock_dir = charoite_paths.secure_dir(
            charoite_paths.graph_backups(graph, "cloud_backup", root=root).parent)
    except OSError as e:
        raise SystemExit(f"замок графа не взять ({e}) — не пишу")
    return file_locks.graph_lock(lock_dir, LOCK_WAIT)


class StatusChanged(Exception):
    """Преобразование изменило бы статус, поставленный человеком или контролем, или
    перестало быть один к одному (`reason`) — тогда статусы не с чем сверить."""

    def __init__(self, changes: list[tuple[int, str, str]], reason: str = ""):
        super().__init__(reason or f"статус изменился бы в строках: {len(changes)}")
        self.changes, self.reason = changes, reason


class _Rewrite:
    """Преобразование одного файла — и для сухого чтения, и для safe_write.rewrite_file.

    Гейт статусов стоит на тексте, который реально переписывается: rewrite_file
    перечитывает файл под снимком, и между сухим чтением и записью его мог поменять
    сосед. Оригинал копируется ДО записи — rewrite_file зовёт преобразование перед
    заменой файла, а при повторе после гонки копия перезаписывается тем текстом, который
    и будет заменён. `text` и `after` — прочитанное и записанное последним вызовом:
    отчёт считается по ним, а не по сухому чтению (Sonnet I1 круга 1 по коду)."""

    def __init__(self, copy_to: pathlib.Path | None = None):
        self.copy_to = copy_to
        self.text: str | None = None
        self.after: str | None = None

    def __call__(self, text: str) -> tuple[str, int]:
        after = normalize(text)
        try:
            changes = task_line.status_changes(text, after)
        except ValueError as e:      # преобразование перестало быть один к одному — не пишем
            raise StatusChanged([], reason=str(e)) from e
        if changes:
            raise StatusChanged(changes)
        self.text, self.after = text, after
        if after != text and self.copy_to is not None:
            charoite_paths.secure_dir(self.copy_to.parent)
            self.copy_to.write_text(text, encoding="utf-8")
        return after, int(after != text)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _note_copy(dest: pathlib.Path, rel: pathlib.Path, before: str, after: str) -> None:
    """Строка манифеста — сразу после записи: прогон, оборванный посреди графа,
    оставляет манифест ровно по переписанным файлам."""
    manifest = dest / "manifest.tsv"
    fresh = not manifest.exists()
    with manifest.open("a", encoding="utf-8") as f:
        if fresh:
            f.write("файл\tsha256 до\tsha256 после\n")
        f.write(f"{rel}\t{_sha(before)}\t{_sha(after)}\n")


def _status_note(rel: pathlib.Path, refusal: StatusChanged) -> str:
    # «не один к одному» — своей строкой: раньше она шла как «статус изменился бы — 0: «<начало
    # файла>» → …», то есть называла статусом то, чего не сверяли
    if refusal.reason:
        return f"{rel}: {refusal.reason} — статусы не сверить, файл не тронут"
    changes = refusal.changes
    shown = "; ".join(f"{i}: «{a.strip()}» → «{b.strip()}»" for i, a, b in changes[:3])
    more = f" и ещё {len(changes) - 3}" if len(changes) > 3 else ""
    return f"{rel}: статус изменился бы — {shown}{more}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Поручения в минутках → формат задач")
    ap.add_argument("--graph", help="путь к графу (по умолчанию sufler.graph_dir)")
    ap.add_argument("--apply", action="store_true", help="записать изменения")
    args = ap.parse_args()

    graph = graph_dir(args.graph)
    if not graph or not graph.is_dir():
        print("граф не найден — пропуск")
        return 0

    lock = contextlib.nullcontext(True)
    dest: pathlib.Path | None = None
    if args.apply:
        try:
            root = require_data_root(__file__)
        except (RootNotNamed, ValueError):
            print("для --apply нужен корень данных: CHAROITE_ROOT", file=sys.stderr)
            return 2
        lock = _graph_lock(graph, root)
        dest = charoite_paths.graph_backups(graph, COPIES_KIND, root=root) / time.strftime("%Y%m%d-%H%M%S")

    files = [p for p in graph.rglob("*.md")
             if "инутк" in p.name or "_minutes" in p.name]
    before = after = changed = written = 0
    refused: list[str] = []
    with lock as taken:
        if not taken:
            print(f"замок графа занят дольше {LOCK_WAIT // 60} мин — не пишу", file=sys.stderr)
            return 1
        for p in files:
            rel = p.relative_to(graph)
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                refused.append(f"{rel}: не прочитан ({e})")
                continue
            final = text
            try:
                fixed, n = _Rewrite()(text)
            except StatusChanged as e:
                refused.append(_status_note(rel, e))
                fixed, n = text, 0
            if n:
                changed += 1
                final = fixed
            if n and dest is not None:
                # Файл читается заново под снимком и пишется с гейтом expect: отметка,
                # сделанная после сухого чтения, не затирается, а чужая запись посреди
                # правки — отказ этого файла, а не обрыв прогона (Sonnet C1 = Opus M1).
                rw = _Rewrite(dest / rel)
                start = text
                try:
                    n = safe_write.rewrite_file(p, rw, "поручения минуток")
                except StatusChanged as e:
                    refused.append(_status_note(rel, e))
                    n, final = 0, text
                except safe_write.LostRace as e:
                    refused.append(f"{rel}: {e}")
                    n, final = 0, text
                else:
                    # отчёт — по тому, что прочитано и записано под снимком, а не по сухому
                    # чтению: между ними файл мог поменять сосед (Opus M4 круга 2)
                    start, final = rw.text, (rw.after if n else rw.text)
                if n:
                    written += 1
                    _note_copy(dest, rel, rw.text, rw.after)
                else:
                    # копия ложится до записи; записи не было — копия осталась бы сиротой без
                    # строки манифеста, а откат по каталогу вернул бы текст поверх правки
                    # соседа (Sonnet I1 = Opus M2 круга 2)
                    (dest / rel).unlink(missing_ok=True)
                text = start
            before += len(CHECKBOX.findall(text))
            after += len(CHECKBOX.findall(final))

    verb = "исправлено" if args.apply else "будет исправлено"
    print(f"файлов минуток: {len(files)}, {verb}: {written if args.apply else changed}")
    print(f"задач видно: {before} → {after} (+{after - before})")
    if written:
        print(f"оригиналы и манифест: {dest}")
    for note in refused[:20]:
        print(f"не тронуто: {note}", file=sys.stderr)
    if len(refused) > 20:
        print(f"не тронуто: и ещё {len(refused) - 20}", file=sys.stderr)
    if not args.apply and changed:
        print("это сухой прогон; чтобы применить — добавьте --apply")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
