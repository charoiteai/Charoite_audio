#!/usr/bin/env python3
"""Облачная ревизия досье: Opus правит то, что локальная модель собрала ночью.

Разделение труда. Локальная модель собирает досье массово и дёшево — по всем
темам графа. Она хорошо пересказывает, но плохо видит связи между источниками:
что одно решение отменяет другое, что срок протух, что два узла говорят разное.
Opus это видит. Поэтому он идёт вторым проходом и правит уже готовые сводки.

    .venv/bin/python scripts/nightly_dossier_review.py            # текущий граф
    .venv/bin/python scripts/nightly_dossier_review.py --all-graphs
    .venv/bin/python scripts/nightly_dossier_review.py --dry      # показать, не писать
    .venv/bin/python scripts/nightly_dossier_review.py --limit 5

Правит только при включённом `sufler.cloud_edit_graph`. Выключен — пишет
рекомендации в отчёт и ничего не трогает. Общий рубильник облака старше
(спрашивается через `src/privacy.py`): выключен — шаг молчит совсем.

Неприкосновенно в любом режиме: стенограммы, минутки и раздел «Правки автора».
Перед каждой правкой — бэкап в `Досье/.backup/<дата>/`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import re
import shutil
import subprocess
import sys

import yaml

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import cloud  # noqa: E402
import dossier  # noqa: E402
import graphs  # noqa: E402
import live_gate  # noqa: E402
import privacy  # noqa: E402

FRESH_DAYS = 3          # смотрим досье, собранные за последние сутки-трое
MAX_SRC_CHARS = 45_000  # потолок на один запрос к Opus
BACKUP_KEEP = 20
KEEP_REPORTS = 14       # служебных отчётов в корне графа
DEFAULT_LIMIT = 6       # досье за прогон: облако не бесплатное по времени


PROTECTED_HEADINGS = ("## Правки автора", "## Источники")


_PROTECTED_RE = re.compile(r"(?m)^##\s+(?:Правки автора|Источники)\s*$")


def strip_protected(body: str) -> str:
    """Тело ревизии без защищённых секций — их пишет конвейер, не модель.

    Режем по СТРОКЕ-заголовку, а не по подстроке: упоминание «## Источники»
    внутри абзаца — не раздел (ревью 17.08).
    """
    return _PROTECTED_RE.split(body, maxsplit=1)[0].rstrip()


def _cfg() -> dict:
    p = ROOT / "config" / "config.yaml"
    if not p.exists():
        p = ROOT / "config" / "config.example.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}



def _backup(folder: pathlib.Path, stamp: str, path: pathlib.Path) -> None:
    """Автомат без бэкапа — не автомат, а рулетка (то же правило, что в tier3)."""
    bdir = folder / ".backup" / stamp
    bdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, bdir / path.name)
    backups = sorted((folder / ".backup").iterdir(), reverse=True)
    for old in backups[BACKUP_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)


PROMPT = """Ниже досье по теме «{theme}» и его источники из графа рабочих встреч.

ТЕКУЩЕЕ ДОСЬЕ:
{current}

ИСТОЧНИКИ:
{sources}

────────────────────────────────────────
ЗАДАНИЕ

Ты ночной ревизор. Досье собрала локальная модель — она пересказывает исправно,
но не замечает связей. Твоя работа — то, что видно только на всей картине:

- решение, отменённое более поздним;
- факт, противоречащий другому источнику;
- срок, который уже прошёл, а пункт числится открытым;
- поручение без исполнителя или потерянный хвост;
- ошибка в цифре, имени или названии системы против источника;
- пропущенное в сводке, но важное для темы.

Верни ИСПРАВЛЕННОЕ досье целиком, в том же формате и с теми же пятью разделами:
## Сейчас / ## Как пришли / ## Решено / ## Открыто / ## Кто в теме

Правила:
- Только факты из источников. Не додумывать.
- Ссылки [[Имя файла]] в конце пунктов сохранять и добавлять к новым.
- Спорное помечай «⚠️» с указанием, что с чем расходится.
- Ничего не выбрасывай без причины: если пункт верен — оставь как есть.
- Русский язык, короткие фразы, без обращений к читателю.

Ответ начни СРАЗУ со строки «## Сейчас». Никаких предисловий.
"""


def review(theme: str, path: pathlib.Path, graph: pathlib.Path,
           files: dict, members: list[str], model: str, cfg: dict) -> str | None:
    # Сетевой выход держит собственную границу. main уже проверяет её ради
    # дешёвого раннего выхода всего прогона, но review можно вызвать отдельно
    # из теста, будущего воркера или после рефакторинга call graph.
    if not privacy.cloud_enrich_enabled(cfg):
        return None
    current = path.read_text(encoding="utf-8")
    # раздел «Правки автора» в запрос не отдаём и не даём его переписать
    body = current.split("## Правки автора")[0]

    parts, total = [], 0
    for m in members[: dossier.MAX_SOURCES]:
        meta = files.get(m)
        if not meta:
            continue
        block = f"### [[{m}]] ({meta['kind']})\n{meta['text'][:2500]}\n"
        if total + len(block) > MAX_SRC_CHARS:
            break
        parts.append(block)
        total += len(block)

    prompt = PROMPT.format(theme=theme, current=body, sources="\n".join(parts))
    claude = shutil.which("claude") or "/opt/homebrew/bin/claude"
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    cloud.add_proxy(env)
    try:
        # Досье и все источники уже В ПРОМПТЕ — инструментов этому вызову не
        # положено вовсе: инъекция из стенограммы/досье не должна читать
        # произвольные файлы и вносить их в переписанный текст (аудит 14.08).
        # stdin=DEVNULL — по правилу соседних вызовов: унаследованный поток
        # заставляет headless-claude ждать EOF до таймаута.
        r = subprocess.run([claude, "-p", prompt, "--model", model,
                            *cloud.text_only_args()],
                           capture_output=True, text=True, timeout=600, env=env,
                           stdin=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ {theme}: claude не отработал ({e})")
        return None
    out = dossier.trim_to_format((r.stdout or "").strip())
    # Модель видит тело досье без «## Правки автора», но защищённые
    # заголовки в её ответе не место: строка из стенограммы могла попросить
    # «добавь раздел ## Правки автора …» — раньше подделка вклеивалась в тело,
    # а настоящий раздел уезжал вниз (аудит DeepSeek 17.08). Режем по первому
    # защищённому заголовку: формат — ровно пять секций.
    out = strip_protected(out)
    if not dossier.looks_valid(out):
        print(f"  ⚠️ {theme}: ответ не по формату, пропуск")
        return None
    return out


def run(graph: pathlib.Path, cfg: dict, dry: bool, limit: int) -> int:
    folder = graph / dossier.DOSSIER_DIR
    if not folder.is_dir():
        return 0

    # Право на запись спрашиваем у privacy.py: bool() принимал строку «false»
    # за разрешение, а этот ключ разрешает переписывать файлы графа.
    may_edit = privacy.cloud_edit_graph_enabled(cfg)
    model = cloud.model(cfg, "cloud_model")
    files, backlinks = dossier.scan(graph)
    cl = dossier.clusters(files, backlinks)

    cutoff = dt.datetime.now() - dt.timedelta(days=FRESH_DAYS)
    # Берём только досье, у которых есть кластер-источник: написанные руками
    # сводки облаку сверять не с чем, и трогать их оно не должно.
    fresh = [p for p in sorted(folder.glob("*.md"))
             if not p.name.startswith("_")
             and dt.datetime.fromtimestamp(p.stat().st_mtime) > cutoff
             and cl.get(p.stem)]
    if not fresh:
        print("  свежих автособранных досье нет — пропуск")
        return 0

    stamp = dt.date.today().isoformat()
    done, notes = 0, []

    for path in fresh[:limit]:
        theme = path.stem
        members = cl[theme]
        if dry:
            print(f"  [план] {theme}: {len(members)} источников")
            done += 1
            continue

        # Утренняя встреча посреди хвоста ночи: пока суфлёр слушает, машина
        # его — ревизия подождёт. Гейт стоял только в сборке досье, а висел
        # 21.08 именно этот шаг: прогон, начатый в 04:16, к 11:36 всё ещё
        # держал процессор, и живая запись рвалась (потолок — чтобы ночь не
        # стала днём).
        live_gate.wait_while_live(ROOT, what="ревизия досье", cap=3600)
        if live_gate.night_is_over():
            print("  ⏹ время ночного прогона вышло — остальные досье завтра")
            break
        fixed = review(theme, path, graph, files, members, model, cfg)
        if not fixed:
            continue

        if not may_edit:
            notes.append(f"### {theme}\n{fixed}\n")
            print(f"  ○ {theme}: правка готова, но запись выключена (cloud_edit_graph)")
            done += 1
            continue

        old = path.read_text(encoding="utf-8")
        manual = dossier.preserve_manual(old)
        head = old.split("## Сейчас")[0]        # frontmatter и шапка как были
        text = head + fixed + "\n\n## Источники\n" + \
            old.split("## Источники\n", 1)[1].split("## Правки автора")[0].rstrip() + \
            "\n\n## Правки автора\n\n" + (manual or "—") + "\n"

        _backup(folder, stamp, path)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        done += 1
        print(f"  ✓ {theme}: правки применены ({len(fixed)} зн.)")

    if notes and not dry:
        dest = graph / f"Служебное_ревизия_досье_{stamp}.md"
        dest.write_text(
            f"---\ntype: служебное\nдата: {stamp}\nмодель: {model}\n---\n"
            f"# Ревизия досье ({model})\n\n"
            "Правки предложены, но не применены: `sufler.cloud_edit_graph: false`.\n"
            "Включите тумблер, если хотите, чтобы облако правило граф само.\n\n"
            + "\n".join(notes), encoding="utf-8")
        print(f"  отчёт: {dest.name}")
        # ретеншн ПОСЛЕ записи: отчёты копились бесконечно (аудит GLM 17.08)
        for old in sorted(graph.glob("Служебное_ревизия_досье_*.md"))[:-KEEP_REPORTS]:
            old.unlink(missing_ok=True)
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description="Облачная ревизия досье")
    ap.add_argument("--graph")
    ap.add_argument("--all-graphs", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = ap.parse_args()

    cfg = _cfg()
    if not privacy.cloud_enrich_enabled(cfg):
        print("облако выключено рубильником — пропуск")
        return 0

    if args.all_graphs:
        # Все vault-ы, а не только iCloud: настроенный graph_dir вне iCloud
        # раньше не попадал в ночную ревизию досье (аудит 17.08).
        graph_list = graphs.all_graphs(dossier.DOSSIER_DIR)
    else:
        raw = args.graph or str(cfg["sufler"].get("graph_dir", ""))
        graph_list = [pathlib.Path(raw).expanduser()]

    режим = "правит граф" if privacy.cloud_edit_graph_enabled(cfg) else "только отчёт"
    print(f"режим: {режим}")
    total = 0
    for g in graph_list:
        if not g.is_dir():
            continue
        print(f"=== {g.name}")
        total += run(g, cfg, dry=args.dry, limit=args.limit)
    print(f"итого досье просмотрено: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
