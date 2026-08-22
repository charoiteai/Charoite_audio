"""Ночной облачный проход по ядрам: Opus смотрит то, что локальной модели не по зубам.

Запускается из nightly.sh ПОСЛЕ tier3 (читает уже причёсанные ядра).
Ничего НЕ правит — пишет отчёт-рекомендации в граф:
`Служебное_ночная_ревизия_YYYY-MM-DD.md` (противоречия между ядрами,
протухшие факты, кандидаты на слияние, потерянные хвосты).

Уважает те же рубильники, что и пост-встречный enrich: sufler.cloud_enrich
и SUFLER_NO_CLOUD — выключены значит молчим. Ядра уходят в Anthropic API.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys

import yaml

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
FRESH_DAYS = 7
MAX_CHARS = 60_000

sys.path.insert(0, str(CODE / "src"))
import cloud  # noqa: E402 — путь к src задаётся строкой выше
import privacy  # noqa: E402


def _cfg() -> dict:
    p = ROOT / "config" / "config.yaml"
    if not p.exists():
        p = ROOT / "config" / "config.example.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))



REPORT_SECTIONS = ("## Противоречия", "## Протухшее", "## Слияния",
                   "## Потерянные хвосты", "## Три риска недели")
KEEP_REPORTS = 14
INDEX_CHARS = 4000
# Что ушло в облако в прошлый раз: {граф: {ядро: mtime}}. Нужно, чтобы ночь
# за ночью показывать НЕ одни и те же ядра (разбор 22.08: при 161 свежем
# ядре на 636 КБ в 60 КБ промпта помещалось 20, и по алфавиту — всегда те же).
SEEN = ROOT / "logs" / "nightly_cores_seen.json"


def _seen(graph: pathlib.Path) -> dict:
    try:
        return json.loads(SEEN.read_text(encoding="utf-8")).get(str(graph), {})
    except Exception:  # noqa: BLE001 — нет файла/битый: как первый запуск
        return {}


def _save_seen(graph: pathlib.Path, sent: dict) -> None:
    try:
        data = json.loads(SEEN.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    data[str(graph)] = sent
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def select_cores(fresh: list[pathlib.Path], seen: dict, budget: int,
                 index_text: str = "") -> tuple[list[pathlib.Path], str]:
    """Какие ядра уходят в промпт и сам текст промпта (без хвоста задания).

    Порядок: сначала ядра, изменившиеся с прошлого прогона (их mtime новее
    записанного), потом остальные — и те и другие по убыванию свежести.
    Бюджет считается по целым ядрам: следующее не помещается — пропускаем его
    и пробуем дальше (короткие ещё влезут), но ни одно не режется посередине.
    Раньше: алфавит и blob[:60_000] — ревизия видела ~4% ядер, всегда «А–В»,
    последнее — обрывком.
    """
    def key(p: pathlib.Path):
        mtime = p.stat().st_mtime
        changed = mtime > float(seen.get(p.stem, 0))
        return (0 if changed else 1, -mtime)
    ordered = sorted(fresh, key=key)
    parts = [f"## ИНДЕКС\n{index_text[:INDEX_CHARS]}"] if index_text else []
    total = sum(len(x) + 2 for x in parts)
    chosen = []
    for p in ordered:
        block = f"## ЯДРО: {p.stem}\n{p.read_text(encoding='utf-8')}"
        if total + len(block) + 2 > budget:
            continue
        parts.append(block)
        total += len(block) + 2
        chosen.append(p)
    return chosen, "\n\n".join(parts)


def report_problem(returncode: int, out: str) -> str:
    """Почему ответ облака НЕ ревизия — пусто, если всё в порядке.

    Контракт промпта: пять секций со строгими заголовками, «по ним парсит
    утренний бриф». Ответ без них — отказ модели, обрыв или сообщение об
    ошибке CLI; код возврата ≠ 0 — тем более.
    """
    if returncode:
        return f"CLI облака завершился с кодом {returncode}"
    if not out.strip():
        return "пустой ответ"
    missing = [h for h in REPORT_SECTIONS if h not in out]
    if missing:
        return "нет секций: " + ", ".join(missing)
    return ""


def prune_reports(graph: pathlib.Path, prefix: str, keep: int = KEEP_REPORTS) -> None:
    """Служебные отчёты копились в корне графа бесконечно (аудит GLM 17.08):
    держим последние keep, старые убираем."""
    reports = sorted(graph.glob(f"{prefix}*.md"))
    for old in reports[:-keep] if len(reports) > keep else []:
        try:
            old.unlink()
        except OSError:
            pass


def main() -> None:
    cfg = _cfg()
    # Решение об отправке принимает только src/privacy.py. Своя проверка,
    # стоявшая здесь раньше, знала одно имя рубильника из двух: после
    # переименования проекта CHAROITE_NO_CLOUD этот скрипт игнорировал —
    # и до 60 000 знаков графа уходили в Anthropic вопреки выключателю,
    # который PRIVACY.md называет перекрывающим любой конфиг.
    if not privacy.cloud_enrich_enabled(cfg):
        print("облако выключено (cloud_enrich / kill-switch) — пропуск")
        return
    graph = pathlib.Path(str(cfg["sufler"].get("graph_dir", ""))).expanduser()
    cores = graph / "Ядра"
    if not cores.is_dir():
        print("ядер нет — пропуск")
        return
    cutoff = dt.datetime.now() - dt.timedelta(days=FRESH_DAYS)
    fresh = [p for p in sorted(cores.glob("*.md"))
             if not p.name.startswith("_") and dt.datetime.fromtimestamp(p.stat().st_mtime) > cutoff]
    if not fresh:
        print("свежих ядер нет — пропуск")
        return
    index = cores / "_ЯДРА.md"
    index_text = index.read_text(encoding="utf-8") if index.exists() else ""
    chosen, blob = select_cores(fresh, _seen(graph), MAX_CHARS, index_text)
    total_chars = sum(len(p.read_text(encoding="utf-8")) for p in fresh)
    # Честный охват в лог: раньше печаталось len(fresh) — число кандидатов,
    # и «ядер 249» читалось как «ревизия видела все 249».
    print(f"в промпт: ядер {len(chosen)} из {len(fresh)} свежих, "
          f"{len(blob)} из {total_chars} знаков")
    if not chosen:
        print("ни одно ядро не поместилось в бюджет — пропуск")
        return

    model = cloud.model(cfg, "cloud_model")
    claude = shutil.which("claude") or "/opt/homebrew/bin/claude"
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    cloud.add_proxy(env)
    prompt = (
        "Ты ночной ревизор графа знаний рабочих встреч. Ниже ядра (сквозные "
        f"темы) за последние {FRESH_DAYS} дней и индекс.\n\n" + blob + "\n\n"
        "Дай отчёт по-русски, кратко и предметно, только то, что нашёл. "
        "Заголовки секций СТРОГО такие (по ним парсит утренний бриф):\n"
        "## Противоречия\n## Протухшее\n## Слияния\n"
        "## Потерянные хвосты\n## Три риска недели\n"
        "Внутри — маркированные пункты со ссылками [[Ядра/…]]. Не выдумывай."
    )
    try:
        # Ревизии не положено НИ ОДНОГО инструмента: ядра и индекс уже в
        # промпте (blob выше), а Read/Grep/Glob, разрешённые прежним
        # контрактом, были вектором инъекции — строка в ядре могла заставить
        # ревизора прочитать произвольный файл и вписать его в отчёт
        # (аудит 14.08). Единый контракт «только текст» — cloud.text_only_args.
        r = subprocess.run(
            [claude, "-p", prompt, "--model", model, *cloud.text_only_args()],
            capture_output=True, text=True, timeout=600, env=env,
            stdin=subprocess.DEVNULL)
        out = (r.stdout or "").strip()
    except Exception as e:  # noqa: BLE001
        print(f"CLI облака не отработал: {e}")
        sys.exit(2)          # авария облака — тоже не ревизия (ревью 17.08)
    problem = report_problem(r.returncode, out)
    if problem:
        # Отказ, лимит или обрыв — не ревизия: раньше любой непустой stdout
        # ложился в граф как «ночная ревизия», а бриф молча терял разделы
        # (аудит 17.08). Не пишем, говорим вслух, код ≠ 0 — ночь увидит.
        print(f"ревизия не принята: {problem} ({(r.stderr or '')[:200]})")
        sys.exit(2)
    day = dt.date.today().isoformat()
    dest = graph / f"Служебное_ночная_ревизия_{day}.md"
    dest.write_text(
        f"---\ntype: служебное\nдата: {day}\nмодель: {model}\n---\n"
        f"# Ночная ревизия ядер ({model})\n\n{out}\n", encoding="utf-8")
    print(f"отчёт: {dest.name} ({len(out)} зн., ядер в ревизии {len(chosen)} из {len(fresh)})")
    _save_seen(graph, {p.stem: p.stat().st_mtime for p in chosen})
    prune_reports(graph, "Служебное_ночная_ревизия_")


if __name__ == "__main__":
    main()
