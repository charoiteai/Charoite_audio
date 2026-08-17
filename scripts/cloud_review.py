#!/usr/bin/env python3
"""Контролируемый запуск облачного разбора встречи: с таймаутом и границами.

Раньше `graph_updater` запускал `claude` фоном и на этом заканчивал. Сообщение
«запущен фоном» означало ровно одно: процесс стартовал. Упал он, упёрся в лимит
или ответил обрывком — в папке встречи оставался пустой или недописанный файл
ревизии, с виду настоящий (CHR-AUD-004). А в режиме записи модель правила граф
напрямую, и бэкап с границами существовали только в тексте промпта, то есть
держались на её послушании (CHR-AUD-003).

Этот воркер запускается фоном вместо самого `claude` и доводит дело до конца:

    ждёт процесс с таймаутом → проверяет код возврата и то, что ответ похож на
    ревизию → публикует файл атомарно;
    в режиме записи снимает бэкап графа ДО запуска и после сверяет, что тронуто
    только разрешённое, а нарушения откатывает.

Границы узкие намеренно. Облако дообогащает граф: узлы, ядра, заметки встреч.
Стенограммы, минутки и раздел «## Правки автора» неприкосновенны — это то, что
написал человек или записала машина с его слов.

    .venv/bin/python scripts/cloud_review.py --stamp 2026-07-15_1400 \\
        --transcript transcripts/2026-07-15_1400.md --graph ~/Vault/Работа \\
        --rev transcripts/2026-07-15_1400_ревизия_claude.md
"""
from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import deps  # noqa: E402

deps.explain_missing()      # запущено не из .venv — скажем рецепт, а не трейсбек

import cloud  # noqa: E402
import graph_updater  # noqa: E402
import privacy  # noqa: E402

BACKUP_DIR = ".cloud_backup"
BACKUP_KEEP = 10            # столько последних бэкапов держим (как в tier3)
TIMEOUT = 30 * 60           # разбор длинной встречи идёт минуты, но не часы
MIN_REPORT = 60             # страховка от «ok» и пустой строки

# Что облако не трогает никогда, даже находясь внутри графа. Архив встречи —
# та же категория, что копии стенограмм: Саммари, Минутки, Стенограмма суфлёра
# и документы, разложенные конвейером. Докстринг выше обещал это всегда, а
# граница покрывала только одну папку из двух.
PROTECTED_DIRS = ("Документация/Стенограммы встреч", "Встречи-архив")
PROTECTED_SECTION = "## Правки автора"


def may_write(path: pathlib.Path, graph: pathlib.Path) -> bool:
    """Можно ли облаку менять этот файл.

    Внутри графа — да, кроме копий стенограмм: их кладёт конвейер, и правка
    там означала бы переписанную запись разговора. Вне графа — никогда:
    конфиг проекта, дневник по соседству и всё остальное облаку не
    принадлежит.
    """
    try:
        rel = path.resolve().relative_to(graph.resolve())
    except (ValueError, OSError):
        return False
    # Скрытые каталоги графа — снимки (.cloud_backup, .forget_backup,
    # Ядра/.tier3_backup) и служебное Obsidian (.obsidian, .trash). Снимок и
    # откат их не видят (snapshot пропускает dot-пути), значит и писать туда
    # облаку нельзя: правка бэкап-истории была бы незаметной и необратимой
    # (аудит DeepSeek 16.08).
    if any(part.startswith(".") for part in rel.parts):
        return False
    as_posix = rel.as_posix()
    return not any(as_posix.startswith(p) for p in PROTECTED_DIRS)


def author_section_changed(before: str, after: str) -> bool:
    """Тронут ли раздел «## Правки автора» — то, что человек писал руками."""
    def tail(text: str) -> str:
        _, sep, rest = text.partition(PROTECTED_SECTION)
        return rest if sep else ""
    return tail(before).strip() != tail(after).strip()


def _digest(path: pathlib.Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def snapshot(graph: pathlib.Path) -> dict[str, str]:
    """Хеши всех файлов графа. Дёшево: граф это текст.

    Именно всех, а не только *.md: граница «что тронуло облако» обязана
    видеть любой файл, иначе .txt или .canvas меняются и создаются мимо
    контроля — снимок по расширению охранял бы не граф, а формат.
    """
    out: dict[str, str] = {}
    for p in graph.rglob("*"):
        if not p.is_file():
            continue
        if BACKUP_DIR in p.parts or any(part.startswith(".") for part in p.parts):
            continue
        out[str(p.resolve())] = _digest(p)
    return out


def changed_since(before: dict[str, str], graph: pathlib.Path) -> list[pathlib.Path]:
    """Что изменилось, появилось или ИСЧЕЗЛО после запуска облака.

    Удалённые — обязательно: diff только по текущим файлам делает удаление
    невидимым, то есть стереть узел облаку было «можно» просто потому, что
    сравнивать становилось нечего.
    """
    now = snapshot(graph)
    touched = [pathlib.Path(k) for k, v in now.items() if before.get(k) != v]
    touched += [pathlib.Path(k) for k in before if k not in now]
    return touched


def backup_graph(graph: pathlib.Path, stamp: str) -> pathlib.Path:
    """Копия файлов графа перед правкой. Обещание PRIVACY — кодом."""
    dest = graph / BACKUP_DIR / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for p in graph.rglob("*"):
        if not p.is_file():
            continue
        if BACKUP_DIR in p.parts or any(part.startswith(".") for part in p.parts):
            continue
        target = dest / p.resolve().relative_to(graph.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    old = sorted((graph / BACKUP_DIR).iterdir(), reverse=True)[BACKUP_KEEP:]
    for stale in old:
        shutil.rmtree(stale, ignore_errors=True)
    return dest


def restore(path: pathlib.Path, graph: pathlib.Path, backup: pathlib.Path) -> bool:
    """Вернуть файл из бэкапа. False — копии не было (файл создан облаком)."""
    try:
        source = backup / path.resolve().relative_to(graph.resolve())
    except (ValueError, OSError):
        return False
    if not source.exists():
        return False
    shutil.copy2(source, path)
    return True


def enforce_boundaries(before: dict[str, str], graph: pathlib.Path,
                       backup: pathlib.Path) -> tuple[list[str], list[str], int]:
    """Сверить граф с состоянием до запуска и убрать запрещённое.

    Возвращает (откачено, удалено, всего правок). Нарушение — это не только
    правка существующего файла: удаление тоже правка (пропавшие «Правки
    автора» не перестают быть авторскими), а файл, СОЗДАННЫЙ облаком в
    защищённой папке, не имеет копии в бэкапе — его нельзя откатить, но
    обязаны убрать, иначе запрет действует только на то, что существовало
    до запуска.
    """
    reverted, removed = [], []
    touched = changed_since(before, graph)
    for path in touched:
        try:
            old = backup / path.resolve().relative_to(graph.resolve())
        except (ValueError, OSError):
            old = backup / path.name        # вне графа — старой копии нет
        old_text = (old.read_text(encoding="utf-8", errors="ignore")
                    if old.exists() else "")
        new_text = (path.read_text(encoding="utf-8", errors="ignore")
                    if path.exists() else "")
        bad = not may_write(path, graph)
        if not bad and old.exists():
            # правка и удаление меряются одинаково: у стёртого файла
            # new_text пуст, и пропавшие «Правки автора» — нарушение
            bad = author_section_changed(old_text, new_text)
        if not bad:
            continue
        if restore(path, graph, backup):
            reverted.append(path.name)
        elif path.exists():
            path.unlink()
            removed.append(path.name)
    return reverted, removed, len(touched)


def looks_like_report(text: str) -> bool:
    """Похоже ли это на ревизию, а не на сообщение об ошибке или обрывок.

    Критерий по СТРУКТУРЕ, а не по числу знаков: короткая, но настоящая
    ревизия бывает («две правки и всё»), а вот однострочник — это всегда либо
    ошибка CLI («Ошибка: rate limit»), либо обрывок. Ревизия — перечень:
    минимум три непустые строки и хотя бы два пункта или заголовок.
    """
    body = (text or "").strip()
    if len(body) < MIN_REPORT:
        return False
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if len(lines) < 3:
        return False
    bullets = sum(1 for ln in lines if ln.lstrip().startswith(("-", "*", "•")))
    return bullets >= 2 or any(ln.startswith("#") for ln in lines)


def publish(tmp: pathlib.Path, rev: pathlib.Path, ok: bool) -> bool:
    """Атомарная публикация. Не удалось — текст сохраняем как .partial.

    Пустой или недописанный файл на месте ревизии хуже отсутствия файла: он
    выглядит как готовый ответ, и человек читает обрывок, не зная об этом.
    """
    if not ok:
        if tmp.exists():
            tmp.replace(rev.with_suffix(rev.suffix + ".partial"))
        return False
    tmp.replace(rev)
    return True


def run(stamp: str, transcript: pathlib.Path, graph: pathlib.Path,
        rev: pathlib.Path, log: pathlib.Path, cfg: dict) -> int:
    # Разрешение спрашиваем ЗДЕСЬ, а не полагаемся на вызывающего: воркер —
    # отдельный процесс, и запустить его можно руками. Рубильник
    # CHAROITE_NO_CLOUD действует и на этом пути.
    if not privacy.cloud_enrich_enabled(cfg):
        print("облако выключено рубильником или конфигом — разбор не запускается")
        return 1
    graph_available = graph_updater.cloud_graph_available(graph)
    # Право править имеет смысл только вместе с узкой cwd=graph. При
    # отсутствующем/слишком широком графе команда уйдёт в text-only режим:
    # папка стенограмм не должна случайно получить файловые инструменты.
    may_edit = privacy.cloud_edit_graph_enabled(cfg) and graph_available
    # Право правки живо только вместе со страховкой: нет каталога графа
    # (несмонтированный iCloud-том) или не получился бэкап — режим чтения.
    # Раньше бэкап тихо пропускался, а Edit/Write всё равно выдавались —
    # ровно то, чего PRIVACY обещает не допускать (ревью 15.08). Понижаем
    # ДО сборки промпта и команды: и задание, и права должны совпадать.
    before, backup = {}, None
    if may_edit:
        if not graph.is_dir():
            print("право правки есть, а каталога графа нет — бэкап невозможен, "
                  "работаю на чтение")
            may_edit = False
        else:
            before = snapshot(graph)
            try:
                backup = backup_graph(graph, stamp)
            except OSError as e:
                print(f"бэкап графа не удался ({e}) — работаю на чтение")
                may_edit = False
    # Файлы встречи — по стему её стенограммы, а не по минутному штампу:
    # посекундная соседка той же минуты в контекст не попадает (аудит 16.08).
    context, sent = graph_updater.cloud_enrich_context(transcript.parent, transcript.stem)
    prompt = graph_updater.cloud_enrich_prompt(
        transcript_name=transcript.name, folder=transcript.parent, graph=graph,
        rev_name=rev.name, stamp=stamp, arch_folder=None, may_edit=may_edit,
        context=context)
    cmd = graph_updater.cloud_enrich_command(
        cfg, claude_bin=shutil.which("claude") or "/opt/homebrew/bin/claude",
        prompt=prompt, model=cloud.model(cfg, "cloud_model"), may_edit=may_edit,
        graph_available=graph_available)

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    tmp = rev.with_suffix(rev.suffix + ".part")
    work_dir = graph_updater.cloud_enrich_workdir(cfg, graph, transcript.parent)
    mode = ("правка графа" if may_edit else
            "только чтение графа" if graph_available else
            "только текст (граф недоступен)")
    with tmp.open("w", encoding="utf-8") as out, log.open("a", encoding="utf-8") as lf:
        lf.write(f"[cloud-review] {stamp}: файлов в запросе {len(sent)} "
                 f"({', '.join(sent)}), {len(context)} знаков, "
                 f"режим {mode}\n")
        lf.flush()
        try:
            code = subprocess.run(cmd, cwd=str(work_dir), env=env,
                                  stdin=subprocess.DEVNULL, stdout=out, stderr=lf,
                                  timeout=TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            lf.write(f"[cloud-review] таймаут {TIMEOUT}с — разбор прерван\n")
            code = -1

    text = tmp.read_text(encoding="utf-8") if tmp.exists() else ""
    ok = code == 0 and looks_like_report(text)
    published = publish(tmp, rev, ok)

    with log.open("a", encoding="utf-8") as lf:
        if published:
            lf.write(f"[cloud-review] ревизия сохранена: {rev.name}\n")
        else:
            lf.write(f"[cloud-review] ревизия НЕ сохранена (код {code}, "
                     f"{len(text)} знаков) — см. {rev.name}.partial\n")
        if may_edit and backup is not None:
            reverted, removed, touched = enforce_boundaries(before, graph, backup)
            lf.write(f"[cloud-review] правок графа: {touched}"
                     + (f", откатано запрещённых: {', '.join(reverted)}"
                        if reverted else "")
                     + (f", удалено созданных в защищённых: {', '.join(removed)}"
                        if removed else "") + "\n")
    return 0 if published else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--transcript", type=pathlib.Path, required=True)
    ap.add_argument("--graph", type=pathlib.Path, required=True)
    ap.add_argument("--rev", type=pathlib.Path, required=True)
    ap.add_argument("--log", type=pathlib.Path, required=True)
    args = ap.parse_args()
    cfg = graph_updater.load_cfg()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    return run(args.stamp, args.transcript, args.graph, args.rev, args.log, cfg)


if __name__ == "__main__":
    sys.exit(main())
