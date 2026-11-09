#!/usr/bin/env python3
"""Откат одного прогона контроля поручений по манифесту рядом с этим файлом.

Возвращает `- [-] … _(снято …)_` обратно в `- [ ] …` только там, где строка до сих пор стоит в
снятом виде: правленное руками после прогона не трогается. Строка ищется по номеру, при сдвиге —
единственная такая же по тексту. Возвращённое дописывается в реестр `returned.jsonl` (путь — в run.json): такие
поручения контроль больше не снимает. Граф и замок — из run.json этого каталога; запись под тем же
замком графа через `safe_write.rewrite_file`. Без --apply — только счёт, без замка.
Стандартная библиотека и хелперы Чароита (тоже на ней); прогон кладёт их снимок в `lib/` рядом,
поэтому откат работает и после удаления checkout. Код 1 — часть строк не найдена (правили после
прогона), есть записи прерванного прогона («не подтверждено»), неоднозначные записи (строку сдвинули
вставкой сверху, а откат уже был — по тексту не отличить) или битые строки манифеста; 0 — возвращено всё.
Известный предел: если после прогона снятую строку изменили руками, а на её прежнее место вставили
чужую с точно таким же текстом, принадлежность по тексту и номеру не доказывается — откат может
принять чужую за свою. Поэтому после ручных правок прогон лучше откатывать вручную по files/.
"""
import datetime as dt
import collections
import json
import os
import pathlib
import sys

here = pathlib.Path(__file__).resolve().parent
run = json.loads((here / "run.json").read_text(encoding="utf-8"))


def _bind_helpers():
    """Хелперы отката: из дерева кода прогона, а если его уже нет — из снимка lib/ рядом.

    Снимок кладёт прогон (`tasks_control.apply`): откат обязан работать, даже когда
    checkout, из которого писали, удалён или переехал."""
    sys.path.insert(0, str(pathlib.Path(run["code"]) / "src"))
    try:
        import file_locks
        from charoite_graph import safe_write
        return file_locks, safe_write
    except Exception:                           # noqa: BLE001 — чужое дерево не обязано импортироваться
        pass
    sys.path.insert(0, str(here / "lib"))
    try:
        import file_locks
        import safe_write
        return file_locks, safe_write
    except Exception:                           # noqa: BLE001 — и снимка нет: откатывать нечем
        sys.exit("откат: нет ни дерева кода из run.json, ни снимка хелперов lib/ рядом")


file_locks, safe_write = _bind_helpers()

graph = pathlib.Path(run["graph"])
registry = pathlib.Path(run.get("returned") or here.parent / "returned.jsonl")
apply = "--apply" in sys.argv
broken = 0                                            # оборванные строки манифеста (обрыв записи прогона)


def rows_of_run() -> list[tuple[str, int, str, str, str]]:
    """Строки манифеста без повторов: JSON Lines, у прогонов до 23.09 — TSV. Оборванная строка
    (прогон убит посреди дозаписи, в том числе посреди буквы) пропускается со счётом, а не роняет
    откат всего прогона: байты режутся по строкам до декодирования. Тем же правилом читает реестр
    `load_returned` в tasks_control.py — этот файл лежит в каталоге прогона сам по себе."""
    global broken
    rows = []
    if (here / "manifest.jsonl").exists():
        for raw in (here / "manifest.jsonl").read_bytes().split(b"\n"):
            if not raw.strip():
                continue
            try:                                      # UnicodeDecodeError — тоже ValueError
                r = json.loads(raw.decode("utf-8"))
                state = r.get("state", "done")        # прогоны до split-журнала состояния не знали
                rows.append((r["file"], r["line"] - 1, r["old"], r["new"], state))
            except (ValueError, KeyError, TypeError):
                broken += 1
    else:
        for line in (here / "manifest.tsv").read_text(encoding="utf-8").split("\n")[1:]:
            if line:
                rel, n, old, new = line.split("\t", 3)
                rows.append((rel, int(n) - 1, old, new, "done"))
    return list(dict.fromkeys(rows))


def append_registry(rel: str, olds: list[str], when: str) -> None:
    """Дописать возвращённые строки в реестр с fsync. Хвост без перевода строки (обрыв прошлой
    дозаписи) сначала закрывается переводом: иначе новая запись склеится с ним и пропадёт."""
    rows = "".join(json.dumps({"file": rel, "old": old, "when": when}, ensure_ascii=False) + "\n" for old in olds)
    with registry.open("ab+") as f:
        f.seek(0, os.SEEK_END)
        if f.tell():
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                rows = "\n" + rows
        f.write(rows.encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


def restore_file(rel: str, edits, back: list, when: str) -> tuple[int, int]:
    """Вернуть строки файла; в `back` — возвращённые. Число не найденных.

    При --apply возвращённое ложится в реестр внутри преобразования: `rewrite_file` зовёт его прямо
    перед записью, на том же тексте, что будет записан, и на второй попытке — заново. Вернувшейся
    строки без записи в реестре не бывает. Обратное бывает: обрыв записи или две проигранные гонки
    (`LostRace`) оставят в реестре строку, которая осталась снятой. Такая запись только освобождает
    поручение от автоснятия — задержка, а не потеря правки. Писать реестр после записи файла нельзя:
    обрыв между ними вернул бы строку без реестра, и следующий прогон снял бы её снова (круг 4)."""
    missing = ambiguous = 0

    def transform(before: str):
        nonlocal missing, ambiguous
        back.clear()
        missing = ambiguous = 0
        lines = before.split("\n")
        pair_total = collections.Counter((old, new) for _, old, new in edits)
        reverted: set[int] = set()                    # что открыли мы сами в этом преобразовании
        for i, old, new in edits:
            # Сначала «уже возвращено»: открытая строка на своём месте — откат этой записи
            # состоялся (возможно, прошлым запуском). Искать `new` раньше нельзя: при двух
            # одинаковых поручениях откат открыл бы чужую закрытую строку с тем же текстом.
            if i < len(lines) and lines[i] == old:
                # Но закрытая строка с тем же текстом ниже означает, что вставка сверху
                # сдвинула строки и откат ещё не сделан. По тексту и номеру эти случаи
                # неотличимы — не трогаем и зовём человека, а не выбираем наугад.
                if any(line == new for line in lines):
                    ambiguous += 1
                continue
            old_elsewhere = any(k != i and k not in reverted and line == old
                                for k, line in enumerate(lines))
            if old_elsewhere:
                # Рядом стоит открытая строка с тем же текстом: нашу уже вернули руками, а
                # закрытая — чужая, либо это сдвиг после одинаковой строки. По тексту и номеру
                # не доказать принадлежность — не угадываем и не трогаем.
                if any(line == new for line in lines):
                    ambiguous += 1
                else:
                    missing += 1
                continue
            if i < len(lines) and lines[i] == new:
                # Закрытых строк с этим текстом больше, чем правок прогона, — часть из них
                # чужие (вставка дубля сверху). По тексту принадлежность не доказать: не угадываем.
                occurrences = sum(1 for line in lines if line == new)
                if occurrences > pair_total[(old, new)]:
                    ambiguous += 1
                    continue
                lines[i] = old
                reverted.add(i)
                back.append(old)
                continue
            new_hits = [k for k, line in enumerate(lines) if line == new]
            if len(new_hits) == 1:
                lines[new_hits[0]] = old
                reverted.add(new_hits[0])
                back.append(old)
                continue
            if len(new_hits) > 1:
                # Ровно столько же закрытых строк, сколько правок прогона, — это правки прогона,
                # сдвинутые вставкой другой строки; строки одинаковы, порядок открытия безопасен.
                if len(new_hits) > pair_total[(old, new)]:
                    ambiguous += 1
                    continue
                lines[new_hits[0]] = old
                reverted.add(new_hits[0])
                back.append(old)
                continue
            if len([k for k, line in enumerate(lines) if line == old]) == 1:
                continue                          # уже возвращено прошлым откатом, не «не найдено»
            missing += 1
            continue
        if apply and back:
            append_registry(rel, back, when)
        return "\n".join(lines), len(back)

    if apply:
        safe_write.rewrite_file(graph / rel, transform, "откат снятия поручений")
    else:
        transform((graph / rel).read_text(encoding="utf-8"))
    return missing, ambiguous


by_file: dict[str, list[tuple[int, str, str, str]]] = {}
for rel, i, old, new, state in rows_of_run():
    by_file.setdefault(rel, []).append((i, old, new, state))
total_back = total_missing = total_unconfirmed = total_ambiguous = 0


def walk() -> None:
    """Вернуть файлы по одному, при --apply — под замком графа: прогон контроля, ждущий замка,
    увидит и возврат, и реестр. Файл, который не записался (исчез, нечитаем, дважды проиграл гонку),
    целиком идёт в «не найдено»."""
    global total_back, total_missing, total_unconfirmed, total_ambiguous
    when = dt.datetime.now().isoformat(timespec="seconds")
    for rel, edits in by_file.items():
        done_rows = [(i, old, new) for i, old, new, state in edits if state == "done"]
        # «Намерение» без подтверждения — прогон прервали между журналом и записью файла:
        # откатывать его нельзя (могло не примениться), но и молчать нельзя — считаем вслух.
        # Считаем кратности, а не множество пар: два одинаковых поручения, из которых
        # записано одно, иначе выглядели бы полностью подтверждёнными.
        intent = collections.Counter((old, new) for _, old, new, state in edits if state != "done")
        confirmed = collections.Counter((old, new) for _, old, new in done_rows)
        total_unconfirmed += sum(max(0, n - confirmed[pair]) for pair, n in intent.items())
        back: list[str] = []
        try:
            missing, ambiguous = restore_file(rel, done_rows, back, when)
        except (OSError, UnicodeDecodeError, safe_write.LostRace):
            total_missing += len(done_rows)
            continue
        total_missing += missing
        total_ambiguous += ambiguous
        total_back += len(back)


if apply:
    with file_locks.graph_lock(pathlib.Path(run["lock_dir"]), 300) as taken:
        if not taken:
            sys.exit("замок графа не взят — откат не записан")
        walk()
else:
    walk()
print(f"{'вернул' if apply else 'можно вернуть'}: {total_back}; не найдено (правили после прогона): {total_missing}"
      + (f"; не подтверждено записей прерванного прогона: {total_unconfirmed} (копии — в files/)"
         if total_unconfirmed else "")
      + (f"; неоднозначных записей (сдвиг или повторный откат): {total_ambiguous} — сверьте вручную"
         if total_ambiguous else "")
      + (f"; битых строк манифеста: {broken}" if broken else ""))
# Частичный откат — не успех: автоматический вызов обязан отличить «вернул всё» от «часть
# правок после прогона изменили руками» или «прогон прерван, часть записей не подтверждена».
# Битые строки манифеста тоже код 1: по ним неизвестно, была ли запись файла.
sys.exit(1 if (total_missing or total_unconfirmed or total_ambiguous or broken) else 0)
