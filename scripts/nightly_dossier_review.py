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
import json
import os
import pathlib
import shutil
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import dossier  # noqa: E402
import privacy  # noqa: E402

VAULT = pathlib.Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents"
FRESH_DAYS = 3          # смотрим досье, собранные за последние сутки-трое
MAX_SRC_CHARS = 45_000  # потолок на один запрос к Opus
BACKUP_KEEP = 20
DEFAULT_LIMIT = 6       # досье за прогон: облако не бесплатное по времени


def _cfg() -> dict:
    p = ROOT / "config" / "config.yaml"
    if not p.exists():
        p = ROOT / "config" / "config.example.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _proxy_env() -> dict:
    try:
        s = json.loads((pathlib.Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8"))
        return {k: v for k, v in s.get("env", {}).items() if "proxy" in k.lower()}
    except Exception:  # noqa: BLE001
        return {}


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
           files: dict, members: list[str], model: str | None) -> str | None:
    model = model or "claude-opus-5"
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
    env.update(_proxy_env())
    try:
        r = subprocess.run([claude, "-p", prompt, "--model", model],
                           capture_output=True, text=True, timeout=600, env=env)
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ {theme}: claude не отработал ({e})")
        return None
    out = dossier.trim_to_format((r.stdout or "").strip())
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
    model = cfg["sufler"].get("cloud_model")
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

        fixed = review(theme, path, graph, files, members, model)
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
        graphs = [p for p in sorted(VAULT.iterdir())
                  if p.is_dir() and not p.name.startswith(".")
                  and (p / dossier.DOSSIER_DIR).is_dir()] if VAULT.is_dir() else []
    else:
        raw = args.graph or str(cfg["sufler"].get("graph_dir", ""))
        graphs = [pathlib.Path(raw).expanduser()]

    режим = "правит граф" if privacy.cloud_edit_graph_enabled(cfg) else "только отчёт"
    print(f"режим: {режим}")
    total = 0
    for g in graphs:
        if not g.is_dir():
            continue
        print(f"=== {g.name}")
        total += run(g, cfg, dry=args.dry, limit=args.limit)
    print(f"итого досье просмотрено: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
