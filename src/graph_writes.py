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

import contextlib  # noqa: F401 — тип в аннотации busy()
import hashlib
import json
import pathlib
import time

LOG_NAME = "graph_writes.jsonl"
KEEP_HOURS = 24
# Дольше двух часов разбор встречи не идёт: самый долгий шаг — облачное
# обогащение с потолком 30 минут. Открытое окно старше этого — сирота от
# `kill -9`, и считать его живым нельзя: иначе один убитый разбор ослепляет
# откат навсегда (круг-6 по PR #439, DS Critical 1).
BUSY_MAX_HOURS = 2


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


def write_text(root: pathlib.Path, graph: pathlib.Path, path: pathlib.Path,
               text: str, *, encoding: str = "utf-8") -> None:
    """Записать файл графа и тут же подписать — одним действием.

    Перечислять писателей оказалось безнадёжно: пятый круг по PR #439 нашёл,
    что подписаны три места из одиннадцати, и каждый новый писатель заводил бы
    дыру заново. Поэтому подпись переехала внутрь записи: кто пишет через эту
    функцию, тот подписан по построению, а тест-страж не даёт писать в граф
    мимо неё.
    """
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(path).write_text(text, encoding=encoding)
    note(root, graph, path)


def copy(root: pathlib.Path, graph: pathlib.Path,
         src: pathlib.Path, dst: pathlib.Path) -> None:
    """Скопировать артефакт в граф и подписать. Метаданные сохраняем, как
    делал copy2: время файла в архиве и в vault должно совпадать с оригиналом."""
    import shutil

    pathlib.Path(dst).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    note(root, graph, dst)


def busy(root: pathlib.Path) -> "contextlib.AbstractContextManager":
    """Отметить окно, пока конвейер работает с графом.

    Подписывать каждый файл оказалось невозможно перечислением: писателей
    семнадцать в пяти модулях, и пятый круг по PR #439 нашёл подписанными три
    из них. Каждый новый писатель заводил бы дыру заново.

    Поэтому гарантия даётся окном, а не списком: разбор встречи объявляет
    «я работаю с T1 по T2», и откат облачной ревизии, чьё окно пересеклось с
    этим, НЕ УДАЛЯЕТ появившиеся файлы — он не может знать, чьи они. Точечные
    подписи (`write_text`, `copy`) остаются поверх: они уточняют, но гарантия
    держится и без них.

    Цена честная: в пересекающемся окне уцелеет и то, что создало облако.
    Такой файл виден в логе поимённо и его можно убрать руками, а потерянную
    заметку встречи вернуть неоткуда, кроме карантина (№119).
    """
    return _Busy(root)


class _Busy:
    def __init__(self, root: pathlib.Path) -> None:
        self._root = root

    def __enter__(self) -> "_Busy":
        _mark(self._root, "start")
        return self

    def __exit__(self, *exc) -> bool:
        _mark(self._root, "end")
        # Чистим здесь: разбор встречи — редкое событие, и это единственная
        # точка, где журнал заведомо не нужен целиком. Без вызова он рос бы
        # вечно, а докстринг обещал обратное (круг-6, DS Minor).
        prune(self._root)
        return False


def _mark(root: pathlib.Path, what: str) -> None:
    try:
        log = _log_path(root)
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "busy": what}) + "\n")
    except OSError:
        pass


def pipeline_was_busy(root: pathlib.Path, since: float) -> bool:
    """Работал ли конвейер с графом в наше окно (или работает сейчас).

    Открытое окно засчитывается, только если оно моложе `BUSY_MAX_HOURS`:
    процесс, убитый `kill -9`, «end» не пишет, и вечная сирота сделала бы
    откат слепым до конца жизни журнала.
    """
    records = _read(root)
    if not records:
        return False
    fresh_enough = time.time() - BUSY_MAX_HOURS * 3600
    ends = sorted(r["t"] for r in records if r.get("busy") == "end")
    for rec in records:
        if not rec.get("busy"):
            continue
        if rec["t"] >= since:
            return True             # старт или конец работы попал в наше окно
        if rec.get("busy") == "start" and rec["t"] >= fresh_enough:
            # окно началось до снимка — считается, только если ещё не закрыто
            if not any(e > rec["t"] for e in ends):
                return True
    return False


def _read(root: pathlib.Path) -> list[dict]:
    """Журнал построчно; битая строка пропускается, а не рушит чтение."""
    log = _log_path(root)
    if not log.is_file():
        return []
    out: list[dict] = []
    try:
        for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and isinstance(rec.get("t"), (int, float)):
                out.append(rec)
    except OSError:
        pass
    return out


def written_since(root: pathlib.Path, since: float) -> dict[str, str]:
    """Путь → отпечаток для записей конвейера после момента since."""
    return {str(r["p"]): str(r.get("h", ""))
            for r in _read(root) if r.get("t", 0) >= since and r.get("p")}


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
