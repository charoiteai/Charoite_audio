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

ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or
                    pathlib.Path(__file__).resolve().parent.parent).expanduser()
FRESH_DAYS = 7
MAX_CHARS = 60_000

sys.path.insert(0, str(ROOT / "src"))
import cloud  # noqa: E402 — путь к src задаётся строкой выше
import privacy  # noqa: E402


def _cfg() -> dict:
    p = ROOT / "config" / "config.yaml"
    if not p.exists():
        p = ROOT / "config" / "config.example.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _proxy_env() -> dict:
    try:
        s = json.loads((pathlib.Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8"))
        return {k: v for k, v in s.get("env", {}).items() if "proxy" in k.lower()}
    except Exception:  # noqa: BLE001
        return {}


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
    parts = []
    index = cores / "_ЯДРА.md"
    if index.exists():
        parts.append(f"## ИНДЕКС\n{index.read_text(encoding='utf-8')[:4000]}")
    for p in fresh:
        parts.append(f"## ЯДРО: {p.stem}\n{p.read_text(encoding='utf-8')}")
    blob = "\n\n".join(parts)[:MAX_CHARS]

    model = cloud.model(cfg, "cloud_model")
    claude = shutil.which("claude") or "/opt/homebrew/bin/claude"
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update(_proxy_env())
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
        r = subprocess.run([claude, "-p", prompt, "--model", model],
                           capture_output=True, text=True, timeout=600, env=env)
        out = (r.stdout or "").strip()
    except Exception as e:  # noqa: BLE001
        print(f"claude не отработал: {e}")
        return
    if not out:
        print(f"пустой ответ ({(r.stderr or '')[:200]})")
        return
    day = dt.date.today().isoformat()
    dest = graph / f"Служебное_ночная_ревизия_{day}.md"
    dest.write_text(
        f"---\ntype: служебное\nдата: {day}\nмодель: {model}\n---\n"
        f"# Ночная ревизия ядер ({model})\n\n{out}\n", encoding="utf-8")
    print(f"отчёт: {dest.name} ({len(out)} зн., ядер {len(fresh)})")


if __name__ == "__main__":
    main()
