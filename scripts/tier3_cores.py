#!/usr/bin/env python3
"""CLI ревизии ядер (ядро — src/tier3.py, там же вся логика и пороги).

    .venv/bin/python scripts/tier3_cores.py                # текущий граф, только отчёт
    .venv/bin/python scripts/tier3_cores.py --mark         # обратимые пометки в графе
    .venv/bin/python scripts/tier3_cores.py --apply        # + слить уверенные дубли
    .venv/bin/python scripts/tier3_cores.py --all-graphs --auto    # ночной режим:
        # слияние ТОЛЬКО при sufler.tier3_auto_apply: true, иначе --mark
    .venv/bin/python scripts/tier3_cores.py --all-graphs --auto --since-last
        # то же, но судятся только ядра, изменившиеся с прошлого прогона
    .venv/bin/python scripts/tier3_cores.py --graph /путь  # конкретный граф

Инкрементальная ревизия после каждой встречи уже встроена в graph_updater —
этот CLI нужен для полного O(n²) прогона (ночная джоба) и ручной проверки.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graphs  # noqa: E402
import live_gate  # noqa: E402
import install_profile  # noqa: E402
import tier3  # noqa: E402

# Отметки последнего прогона по графам. Лежат рядом с nightly.json: читает их
# только этот скрипт, но человеку, который разбирается, почему ночь молчала,
# они нужны там же, где остальные следы ночного цикла.
# CHAROITE_ROOT: в бандловой установке код лежит в read-only .app, и запись
# отметок рядом с ним падала бы PermissionError (ревью 19.08, третий круг).
STAMPS = (pathlib.Path(os.environ.get("CHAROITE_ROOT")
                       or pathlib.Path(__file__).resolve().parent.parent)
          / "logs" / "tier3_last_run.json")


def _stamps() -> dict:
    try:
        return json.loads(STAMPS.read_text(encoding="utf-8"))
    except Exception:
        # нет файла или он покорёжен — ведём себя как при первом запуске:
        # полный прогон честнее, чем тихо ничего не разобрать
        return {}


def _save_stamp(graph: pathlib.Path, ts: float) -> None:
    data = _stamps()
    data[str(graph)] = ts
    STAMPS.parent.mkdir(parents=True, exist_ok=True)
    STAMPS.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                      encoding="utf-8")


def run(graph: pathlib.Path, apply: bool, mark: bool = False,
        since_last: bool = False) -> None:
    started = time.time()
    only = None
    if since_last:
        prev = _stamps().get(str(graph))
        if prev is None:
            print(f"=== {graph.name}: отметки нет — полный прогон", flush=True)
        else:
            only = tier3.changed_since(graph / "Ядра", prev)
            if not only:
                print(f"{graph.name}: свежих ядер нет — пропуск", flush=True)
                _save_stamp(graph, started)
                return
            print(f"=== {graph.name}: инкремент, свежих ядер {len(only)}",
                  flush=True)
    r = tier3.revise(graph, only_names=only, apply=apply, mark=mark)
    # Отметку двигаем только после состоявшегося прогона: без NLI-модели или с
    # лежащей Ollama ревизия молча возвращает пустой результат, и сдвинутая
    # отметка вычеркнула бы эти ядра из фокуса навсегда.
    if r["ran"] and not r.get("stopped"):
        _save_stamp(graph, started)
    elif r.get("stopped"):
        # Ревизию оборвал потолок ночи: судимое досмотрено, отметка стоит
        # на месте — завтра инкремент возьмёт те же свежие ядра заново.
        print(f"{graph.name}: ревизия остановлена потолком ночи — "
              "отметка не сдвинута", flush=True)
    n = sum(len(r[k]) for k in ("dups", "nests", "border"))
    took = time.time() - started
    if not r["ran"]:
        # «Чисто» и «не состоялась» — разные ночи: без NLI-модели, с лежащей
        # Ollama или пустыми эмбеддингами ревизия ничего не смотрела, и лог,
        # печатавший «чисто», врал (аудит DeepSeek 17.08).
        print(f"{graph.name}: ревизия не состоялась — нет NLI-модели, "
              f"лежит Ollama или пустые эмбеддинги; отметка не сдвинута ({took:.0f} с)",
              flush=True)
        return
    if not n and not r["log"]:
        print(f"{graph.name}: чисто ({took:.0f} с)", flush=True)
        return
    print(f"=== {graph.name} ({took:.0f} с)")
    for k, title in (("dups", "ДУБЛИ"), ("nests", "ВЛОЖЕНИЯ"), ("border", "ГРАНИЦА")):
        for line in r[k]:
            print(f"  [{title}] {line}")
    for line in r["log"]:
        print(f"  {line}")
    sys.stdout.flush()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", type=pathlib.Path, default=None)
    ap.add_argument("--all-graphs", action="store_true",
                    help="все папки vault с подпапкой Ядра")
    ap.add_argument("--apply", action="store_true",
                    help="слить уверенные дубли (включает --mark)")
    ap.add_argument("--mark", action="store_true",
                    help="обратимые правки: пометки «возможный дубль» и ссылки вложений")
    ap.add_argument("--auto", action="store_true",
                    help="режим из конфига: слияние только при sufler.tier3_auto_apply: true, "
                         "иначе только --mark. Для ночной джобы: право на необратимое — "
                         "у пользователя в конфиге, не у cron")
    ap.add_argument("--since-last", action="store_true",
                    help="судить только ядра, изменившиеся с прошлого прогона (O(k×n) "
                         "вместо O(n²)): полный прогон на выросшем графе съедает всю "
                         "ночь, а свежих ядер за сутки — единицы. Первый запуск и "
                         "потерянная отметка = полный прогон")
    args = ap.parse_args()

    # Профиль мог выключить ревизию (`sufler.tier3: false`): она судит ядра
    # эмбеддингами и поднимает bge-m3 (+1.2 ГБ). Гейт в graph_updater закрывал
    # только путь «после встречи», а ночь ходит сюда и подняла бы эмбеддер по
    # всем графам (ревью 19.08, GLM). Гейтим ровно ночной путь (`--auto`):
    # ручной запуск человек делает осознанно и вправе получить ревизию, даже
    # если фоновая выключена (третий круг, Gemini).
    if args.auto and not install_profile.tier3_enabled(graphs.load_config()):
        print("ревизия ядер выключена профилем (sufler.tier3: false)")
        return

    apply_mode = args.apply
    mark_mode = args.mark
    if args.auto:
        apply_mode = apply_mode or tier3.auto_apply_allowed(graphs.load_config())
        mark_mode = True

    if args.all_graphs:
        found = graphs.all_graphs("Ядра")
        if not found:
            # НЕ sys.exit: этим ходит ночная джоба, а «ревизовать нечего» —
            # не авария. Раньше отсутствие ровно iCloud-папки красило launchd
            # каждую ночь у любого, кто держит граф в другом месте.
            print(f"нет графов с папкой «Ядра» — искал в {graphs.where()}")
            return
        for g in found:
            if live_gate.night_is_over():
                print("⏹ время ночного прогона вышло — остальные графы завтра")
                break
            run(g, apply_mode, mark_mode, args.since_last)
        return
    run(args.graph or graphs.configured_graph() or pathlib.Path.cwd(),
        apply_mode, mark_mode, args.since_last)


if __name__ == "__main__":
    main()
