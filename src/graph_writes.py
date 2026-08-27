"""Журнал записей конвейера в граф: кто положил файл — мы или облако.

Зачем. Облачная ревизия при невалидном ответе откатывает граф к снимку, а
«изменилось с момента снимка» — это не «написало облако»: замок графа облако
держит все тридцать минут ожидания модели, конвейер его не берёт и пишет
рядом (разбор следующей встречи, ретрай из приложения, доклейка минуток).
27.08 такой откат унёс заметку встречи 10:32 и пять артефактов встречи 11:33
(№119).

Отличить по имени файла не выходит: имя со штампом подделывается, и файл,
созданный облаком в защищённой папке, обязан уехать в карантин. Три круга
голов подряд ломали именно догадки по имени и по наличию оригинала — поэтому
признак сделан внешним и точным: кто пишет, тот и отмечается.

Формат — строки JSON, по одной на запись: время и путь относительно графа.
Файл живёт в logs/, чистится по возрасту: журнал старше суток никому не нужен,
а откат смотрит только своё окно.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import time

LOG_NAME = "graph_writes.jsonl"
KEEP_HOURS = 24


def _log_path(root: pathlib.Path) -> pathlib.Path:
    return root / "logs" / LOG_NAME


def note(root: pathlib.Path, graph: pathlib.Path, *paths: pathlib.Path) -> None:
    """Отметить, что эти файлы графа записал конвейер. Молча при любой беде:
    журнал — страховка отката, а не часть разбора встречи."""
    try:
        log = _log_path(root)
        log.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        with log.open("a", encoding="utf-8") as f:
            for p in paths:
                try:
                    rel = str(pathlib.Path(p).resolve().relative_to(graph.resolve()))
                except (ValueError, OSError):
                    rel = pathlib.Path(p).name
                f.write(json.dumps({"t": now, "p": rel, "h": digest(p)},
                                    ensure_ascii=False) + "\n")
    except OSError:
        pass


def digest(path: pathlib.Path) -> str:
    """Отпечаток содержимого — чтобы отметка не покрывала чужую правку.

    Путь и время не отвечают на вопрос «это всё ещё наш файл»: облако может
    переписать отмеченный файл через Write, и по одному пути откат посчитал бы
    версию облака работой конвейера (круг-4 по PR #439, GLM Critical 2).
    """
    try:
        return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def written_since(root: pathlib.Path, since: float) -> dict[str, str]:
    """Путь → отпечаток для записей конвейера после момента since."""
    out: dict[str, str] = {}
    log = _log_path(root)
    if not log.is_file():
        return out
    try:
        for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue        # обрыв строки на записи — пропускаем одну, не весь журнал
            if isinstance(rec, dict) and rec.get("t", 0) >= since and rec.get("p"):
                out[str(rec["p"])] = str(rec.get("h", ""))
    except OSError:
        pass
    return out


def prune(root: pathlib.Path, keep_hours: int = KEEP_HOURS) -> None:
    """Оставить в журнале только свежие записи."""
    log = _log_path(root)
    if not log.is_file():
        return
    cutoff = time.time() - keep_hours * 3600
    try:
        kept = [ln for ln in log.read_text(encoding="utf-8", errors="ignore").splitlines()
                if _fresh(ln, cutoff)]
        tmp = log.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        tmp.replace(log)
    except OSError:
        pass


def _fresh(line: str, cutoff: float) -> bool:
    try:
        return json.loads(line).get("t", 0) >= cutoff
    except ValueError:
        return False
