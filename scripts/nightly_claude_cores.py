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
import time

import yaml

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
FRESH_DAYS = 7
MAX_CHARS = 60_000

sys.path.insert(0, str(CODE / "src"))
import charoite_paths  # noqa: E402 — путь к src задаётся строкой выше
import cloud  # noqa: E402
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
# Что ушло в облако и когда: {граф: {ядро: {"mtime": …, "shown": …}}}.
# Нужно, чтобы ночь за ночью показывать НЕ одни и те же ядра (разбор 22.08:
# при 161 свежем ядре на 636 КБ в 60 КБ промпта помещалось 20, и по
# алфавиту — всегда те же). Карта копится и сливается, а не заменяется
# партией ночи (круг-1 по PR #380: замена ломала ротацию — показанное
# позавчера считалось «новым» и вытесняло никогда не показанное).
SEEN = ROOT / "logs" / "nightly_cores_seen.json"


def _graph_key(graph: pathlib.Path) -> str:
    return str(pathlib.Path(graph).expanduser().resolve())


def _seen_all() -> dict:
    try:
        return json.loads(SEEN.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — нет файла/битый: как первый запуск
        return {}


def _migrate(entry: dict) -> dict:
    """Карта графа в актуальном формате: число (старая запись) → {mtime, shown: 0}."""
    return {k: (v if isinstance(v, dict) else {"mtime": float(v or 0), "shown": 0})
            for k, v in entry.items()}


def _seen(graph: pathlib.Path) -> dict:
    return _migrate(_seen_all().get(_graph_key(graph), {}))


def _graph_gone(key: str) -> bool:
    """Граф удалён — а не временно недоступен: родитель на месте, папки нет.

    Отмонтированный диск или не подтянувшийся iCloud выглядят как «папки
    нет», и стирать по ним единственную память ротации нельзя (круг-3
    по PR #380, DeepSeek).
    """
    p = pathlib.Path(key)
    return not p.is_dir() and p.parent.exists()


def _save_seen(graph: pathlib.Path, sent: dict, current_stems: set[str]) -> None:
    """Слить партию ночи с картой и выбросить ядра, которых больше нет.

    Файл — 0600 и через временный + rename: логи несут темы встреч, а
    обрыв между truncate и записью обнулил бы курсор (круг-1 по PR #380).
    Файл читается ОДИН раз: чужие графы и свой — из одного снимка, иначе
    параллельный прогон, записавший карту между двумя чтениями, терял
    свой граф (круг-3, DeepSeek).
    """
    all_data = _seen_all()
    data = {k: v for k, v in all_data.items() if not _graph_gone(k)}   # удалённые графы — вон
    key = _graph_key(graph)
    entry = _migrate(all_data.get(key, {}))
    entry.update(sent)
    if pathlib.Path(key).is_dir():          # граф исчез во время запроса — не воскрешать ключ
        data[key] = {k: v for k, v in entry.items() if k in current_stems}
    else:
        data.pop(key, None)
    charoite_paths.secure_dir(SEEN.parent)  # 0700 и для уже существующего каталога
    tmp = SEEN.with_name(SEEN.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)   # режим в os.open действует только при создании файла
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, SEEN)


def select_cores(fresh: list[pathlib.Path], seen: dict, budget: int,
                 index_text: str = "",
                 ) -> tuple[list[pathlib.Path], str, dict, list[tuple[pathlib.Path, bool]]]:
    """Какие ядра уходят в промпт: (выбранные, текст, партия для курсора,
    не влезшие как (ядро, не влезло бы и в одиночку)).

    Порядок: сначала ядра, которых курсор не видел или которые изменились
    после показа (mtime новее записанного) — по убыванию свежести; потом
    остальные — по давности показа, самые давние первыми. Одно место —
    второе, сразу за первым ПРИНЯТЫМ изменившимся ядром — всегда у самого
    давно показанного неизменившегося: иначе при постоянном притоке новых
    неизменившиеся не доходят до облака никогда (круг-2 по PR #380, Codex;
    «за принятым, а не за кандидатом» — круг-3: первое изменившееся могло
    не влезть, и резерв обходил влезающее). Бюджет считается по целым
    ядрам: следующее не помещается — пропускаем и пробуем дальше (короткие
    ещё влезут), но ни одно не режется посередине. mtime для курсора
    берётся здесь же, в момент чтения: изменится файл во время запроса к
    облаку — следующая ночь увидит его как новый, а не как показанный.
    Раньше: алфавит и blob[:60_000] — ревизия видела ~4% ядер, всегда
    «А–В», последнее — обрывком.
    """
    stamped = []
    for p in fresh:
        mtime = p.stat().st_mtime
        rec = seen.get(p.stem) or {}
        changed = mtime > float(rec.get("mtime", 0))
        stamped.append((p, mtime, changed, float(rec.get("shown", 0))))
    queue = sorted((s for s in stamped if s[2]), key=lambda s: -s[1])
    rest = sorted((s for s in stamped if not s[2]), key=lambda s: s[3])
    reserve = rest.pop(0) if queue and rest else None
    parts = [f"## ИНДЕКС\n{index_text[:INDEX_CHARS]}"] if index_text else []
    base = sum(len(x) + 2 for x in parts)
    total = base
    chosen, sent, skipped = [], {}, []
    now = time.time()
    while queue or reserve or rest:
        if reserve and (chosen or not queue):    # сразу за первым принятым
            s, reserve = reserve, None
        elif queue:
            s = queue.pop(0)
        else:
            s = rest.pop(0)
        p, mtime = s[0], s[1]
        block = f"## ЯДРО: {p.stem}\n{p.read_text(encoding='utf-8')}"
        if total + len(block) + 2 > budget:
            # too_big — тем же расчётом: в одиночку с индексом не влезает
            skipped.append((p, base + len(block) + 2 > budget))
            continue
        parts.append(block)
        total += len(block) + 2
        chosen.append(p)
        sent[p.stem] = {"mtime": mtime, "shown": now}
    return chosen, "\n\n".join(parts), sent, skipped


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
    chosen, blob, sent, skipped = select_cores(fresh, _seen(graph), MAX_CHARS, index_text)
    total_chars = sum(len(p.read_text(encoding="utf-8")) for p in fresh)
    # Честный охват в лог: раньше печаталось len(fresh) — число кандидатов,
    # и «ядер 249» читалось как «ревизия видела все 249».
    print(f"в промпт: ядер {len(chosen)} из {len(fresh)} свежих, "
          f"{len(blob)} из {total_chars} знаков")
    # Пропущенное по размеру — отдельно: такое ядро не увидит ни одна ночь,
    # и молчать об этом нельзя (круг-1 по PR #380, DeepSeek).
    too_big = [p.stem for p, alone in skipped if alone]
    if too_big:
        print("не влезают целиком ни в одну ночь: " + ", ".join(too_big))
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
    # 0600: отчёт несёт темы встреч, как и логи (круг-2 по PR #380, DS).
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"---\ntype: служебное\nдата: {day}\nмодель: {model}\n---\n"
                f"# Ночная ревизия ядер ({model})\n\n{out}\n")
    print(f"отчёт: {dest.name} ({len(out)} зн., ядер в ревизии {len(chosen)} из {len(fresh)})")
    # Чистка карты — по всем ядрам графа, не только свежим: несвежее, но
    # живое ядро память о показе не теряет (круг-2 по PR #380, Codex).
    _save_seen(graph, sent, {p.stem for p in cores.glob("*.md") if not p.name.startswith("_")})
    prune_reports(graph, "Служебное_ночная_ревизия_")


if __name__ == "__main__":
    main()
