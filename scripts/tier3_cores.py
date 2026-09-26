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
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graphs  # noqa: E402
import nli  # noqa: E402
import llm  # noqa: E402
import live_gate  # noqa: E402
import install_profile  # noqa: E402
import tier3  # noqa: E402
from charoite_paths import harden_umask, resolve_root  # noqa: E402


# Отметки последнего прогона по графам. Лежат рядом с nightly.json: читает их
# только этот скрипт, но человеку, который разбирается, почему ночь молчала,
# они нужны там же, где остальные следы ночного цикла.
# В корне ДАННЫХ (в бандловой установке код лежит в read-only .app, и запись
# отметок рядом с ним падала бы PermissionError — ревью 19.08, третий круг);
# где он, отвечает канон — переменная уже поставлена тем, кто запустил.
def stamps_path() -> pathlib.Path:
    """Файл отметок ночи. Корень спрашивается НА ВЫЗОВЕ, а не на импорте:
    снапшот запоминал ответ канона раньше, чем точка входа назвала корень, и
    подмена корня после импорта до файла уже не доходила (правило №338)."""
    return resolve_root(__file__) / "logs" / "tier3_last_run.json"


def _stamps() -> dict:
    try:
        return json.loads(stamps_path().read_text(encoding="utf-8"))
    except Exception:
        # нет файла или он покорёжен — ведём себя как при первом запуске:
        # полный прогон честнее, чем тихо ничего не разобрать
        return {}


def _pending(graph: pathlib.Path) -> list[str]:
    """Ядра, чьи пары в прошлый раз не судились: вернуть их в фокус адресно."""
    data = _stamps()
    val = data.get(str(graph) + "#pending")
    return list(val) if isinstance(val, list) else []


def _judged(graph: pathlib.Path) -> set[frozenset]:
    """Пары, досуженные оборванным прогоном: следующая ночь их не судит снова."""
    val = _stamps().get(str(graph) + "#judged")
    return {frozenset(p) for p in val if isinstance(p, list) and len(p) == 2} \
        if isinstance(val, list) else set()


def _save_stamp(graph: pathlib.Path, ts: float,
                pending: set[str] | None = None,
                judged: set[frozenset] | None = None) -> None:
    data = _stamps()
    data[str(graph)] = ts
    # Список целиком. Обрезка до 200 обещала, что остальные «вернутся как
    # обычные свежие», но свежесть — это mtime новее отметки, а отметка только
    # что ушла вперёд: 201-е имя не возвращалось никогда (входной круг №358,
    # Codex I3). Размер ограничен числом ядер графа, а не ростом ночей.
    data[str(graph) + "#pending"] = sorted(pending or set())
    # Досуженные пары оборванного прогона — пока очередь не досмотрена целиком;
    # полный прогон их снимает (пустой список)
    data[str(graph) + "#judged"] = sorted(sorted(p) for p in (judged or set()))
    файл = stamps_path()
    файл.parent.mkdir(parents=True, exist_ok=True)
    файл.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                      encoding="utf-8")


def run(graph: pathlib.Path, apply: bool, mark: bool = False,
        since_last: bool = False) -> str:
    """Ревизия одного графа -> исход: «complete», «stopped», «no_work» или
    «unavailable» (как `status` у tier3.revise)."""
    started = time.time()
    only = None
    skip: set[frozenset] = set()
    if since_last:
        prev = _stamps().get(str(graph))
        if prev is None:
            print(f"=== {graph.name}: отметки нет — полный прогон", flush=True)
        else:
            only = tier3.changed_since(graph / "Ядра", prev)
            # Досуженное прошлой ночью годно, пока оба ядра пары не менялись
            свежие = set(only)
            skip = {pair for pair in _judged(graph) if not (pair & свежие)}
            # Пары, не судившиеся в прошлый раз из-за сбоя NLI: они не
            # «свежие» по времени, но досмотреть их обязаны.
            stuck = [n for n in _pending(graph) if n not in only]
            if stuck:
                print(f"{graph.name}: возвращаю в фокус {len(stuck)} ядер "
                      "после прошлых сбоев NLI", flush=True)
                only = list(only) + stuck
            if not only:
                print(f"{graph.name}: свежих ядер нет — пропуск", flush=True)
                _save_stamp(graph, started)
                return "no_work"
            print(f"=== {graph.name}: инкремент, свежих ядер {len(only)}",
                  flush=True)
    # Конфиг обязателен: без него ревизия берёт дефолтную модель эмбеддингов, а
    # дневной путь (graph_updater) — ту, что выбрал владелец. Две шкалы на одном
    # графе значат, что найденный одним прогоном дубль невидим другому
    # (круг 5 по №321, DS I1).
    cfg = graphs.load_config()
    # ночное окно вшито в дверь приложения — то же, что у дневного пути (№365)
    r = graphs.revise_cores(graph, only_names=only, apply=apply, mark=mark,
                            embedder=llm.embedder(cfg, keep_alive=tier3.TIER3_KEEP_ALIVE),
                            judge=nli.judge(), skip_pairs=frozenset(skip))
    # Отметку двигаем только после состоявшегося прогона: без NLI-модели или с
    # лежащей Ollama ревизия молча возвращает пустой результат, и сдвинутая
    # отметка вычеркнула бы эти ядра из фокуса навсегда.
    if r["ran"]:
        # Отметка идёт вперёд и при сбоях, и при обрыве потолком — иначе одна
        # вечно падающая пара или очередь длиннее ночи держали бы инкремент на
        # месте (круг 1 по коду №358: при обрыве отметка стояла, и каждую ночь
        # судились те же верхние пары). Долг — несудившиеся и недосмотренные
        # ядра — лежит рядом с отметкой и вернётся в фокус адресно.
        долг = set(r.get("failed_names") or ()) | set(r.get("unjudged_names") or ())
        досужено = (skip | set(r.get("judged_pairs") or ())) if r.get("stopped") else set()
        _save_stamp(graph, started, долг, досужено)
        if r.get("failed"):
            print(f"{graph.name}: {r['failed']} пар не судились — "
                  f"{len(r.get('failed_names') or ())} ядер вернутся в фокус "
                  "следующим прогоном", flush=True)
        if r.get("stopped"):
            print(f"{graph.name}: ревизия остановлена потолком ночи — "
                  f"{len(r.get('unjudged_names') or ())} ядер досмотрим следующей ночью",
                  flush=True)
    n = sum(len(r[k]) for k in ("dups", "nests", "border"))
    took = time.time() - started
    status = r.get("status") or ("complete" if r["ran"] else "unavailable")
    if status == "no_work":
        # Судить нечего (одно ядро, нет папки) — это не сбой и не «лежит
        # Ollama» (входной круг №358, Codex I4).
        print(f"{graph.name}: ревизовать нечего — {r.get('reason')} ({took:.0f} с)", flush=True)
        return "no_work"
    if not r["ran"]:
        # «Чисто» и «не состоялась» — разные ночи (аудит DeepSeek 17.08), и у
        # «не состоялась» есть причина: её называет ревизия, а не общая фраза
        # «нет NLI-модели, лежит Ollama или пустые эмбеддинги» — месяц она
        # стояла над HTTP 400 от эмбеддера (№358).
        print(f"{graph.name}: ревизия не состоялась — {r.get('reason') or 'причина не названа'}; "
              f"отметка не сдвинута ({took:.0f} с)", flush=True)
        return "unavailable"
    if not n and not r["log"]:
        # «Чисто» — только про досмотренный граф: после обрыва это была бы
        # вторая строка, спорящая с первой (круг 1 по коду, Opus M1)
        if r.get("stopped"):
            print(f"{graph.name}: до потолка ночи находок нет ({took:.0f} с)", flush=True)
            return "stopped"
        print(f"{graph.name}: чисто ({took:.0f} с)", flush=True)
        return "complete"
    print(f"=== {graph.name} ({took:.0f} с)")
    for k, title in (("dups", "ДУБЛИ"), ("nests", "ВЛОЖЕНИЯ"), ("border", "ГРАНИЦА")):
        for line in r[k]:
            print(f"  [{title}] {line}")
    for line in r["log"]:
        print(f"  {line}")
    sys.stdout.flush()
    return "stopped" if r.get("stopped") else "complete"


#: Коды возврата ночного шага — nightly.sh различает их по номеру.
EXIT_UNAVAILABLE = 2    # хоть один граф не ревизован: судить было нечем
EXIT_STOPPED = 4        # потолок ночи оборвал ревизию или очередь графов (3 — занят
                        # в тестах ночи как «упал»; 1 — падение питона)


def exit_code(outcomes: list[str], cut: bool = False) -> int:
    """Итог по всем графам. Успех — только когда КАЖДЫЙ граф досмотрен или
    ему нечего судить: `any(ran)` давал 0, если малый граф прошёл, а основной
    нет, — и ночь месяц не видела, что ревизия основного графа не идёт
    (входной круг №358, Codex C1). Оборванный потолком прогон — не успех."""
    if "unavailable" in outcomes:
        return EXIT_UNAVAILABLE
    if cut or "stopped" in outcomes:
        return EXIT_STOPPED
    return 0


def main() -> int:
    harden_umask()   # отчёт ревизии ядер графа — только владельцу (№385)
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
        return 0        # выключено осознанно — не «вхолостую»

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
            return 0
        outcomes, cut = [], False
        for g in found:
            if live_gate.night_is_over():
                print("⏹ время ночного прогона вышло — остальные графы завтра")
                cut = True
                break
            outcomes.append(run(g, apply_mode, mark_mode, args.since_last))
        # Код 2 — «шаг прошёл вхолостую»: без NLI-модели или с лежащей Ollama
        # ревизия ничего не смотрит, а ночь показывала «ok». У досье такой
        # код есть с самого начала (аудит ночи 26.08, DS Important 4).
        return exit_code(outcomes, cut)
    target = pathlib.Path(args.graph or graphs.configured_graph() or pathlib.Path.cwd())
    if not (target / "Ядра").is_dir():
        # Код 2 значит «модель не отвечала»; отсутствие графа — другая беда
        # и не авария, как и в ветке --all-graphs (круг-2 DS, M3).
        print(f"в {target} нет папки «Ядра» — ревизовать нечего")
        return 0
    return exit_code([run(target, apply_mode, mark_mode, args.since_last)])


if __name__ == "__main__":
    sys.exit(main())
