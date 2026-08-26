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
import collections
import datetime as dt
import os
import pathlib
import re
import shutil
import subprocess
import sys

# Код и данные — разные корни: CHAROITE_ROOT переносит ДАННЫЕ, а `src/`
# всегда лежит рядом с этим файлом. См. src/charoite_paths.py.
CODE = pathlib.Path(__file__).resolve().parent.parent
ROOT = pathlib.Path(os.environ.get("CHAROITE_ROOT") or CODE).expanduser()
sys.path.insert(0, str(CODE / "src"))
import cloud  # noqa: E402
import dossier  # noqa: E402
import graphs  # noqa: E402
import live_gate  # noqa: E402
import tier3  # noqa: E402
import privacy  # noqa: E402
from config_loader import load_user_or_example  # noqa: E402

FRESH_DAYS = 3          # смотрим досье, собранные за последние сутки-трое
MAX_SRC_CHARS = 45_000  # потолок на один запрос к Opus
BACKUP_KEEP = 20
KEEP_REPORTS = 14       # служебных отчётов в корне графа
DEFAULT_LIMIT = 6       # досье за прогон: облако не бесплатное по времени
SECTIONS = ("## Сейчас", "## Как пришли", "## Решено", "## Открыто", "## Кто в теме")
# Ревизия короче 60% прежнего тела — не правка, а потеря: модель ответила
# обрывком, «подытожила» или выбросила разделы. Вверх не ограничиваем —
# настоящая ревизия растёт (замер 22.08: 4,2 → 11,4 КБ, 25 пометок ⚠️).
MIN_KEEP = 0.6


PROTECTED_HEADINGS = ("## Правки автора", "## Источники")


# Заголовок любого уровня и без пробела тоже: «### Правки автора» и
# «##Правки автора» строка из стенограммы подсунет так же легко, как «## »,
# а старый регэксп их пропускал (круг-1 по PR #382, DeepSeek Critical).
_PROTECTED_RE = re.compile(r"(?m)^#{1,6}\s*(?:Правки автора|Источники)\s*$")
_SOURCES_RE = re.compile(r"(?m)^#{1,6}\s*Источники\s*$")
_MANUAL_RE = re.compile(r"(?m)^#{1,6}\s*Правки автора\s*$")
_HEADING_RE = re.compile(r"(?m)^\s*#.*$")


def strip_protected(body: str) -> str:
    """Тело ревизии без защищённых секций — их пишет конвейер, не модель.

    Режем по СТРОКЕ-заголовку, а не по подстроке: упоминание «## Источники»
    внутри абзаца — не раздел (ревью 17.08).
    """
    return _PROTECTED_RE.split(body, maxsplit=1)[0].rstrip()


def split_dossier(text: str) -> tuple[str, str, str | None, str | None]:
    """(шапка до «## Сейчас», тело пяти разделов, источники, правки автора).

    Всё — по якорным заголовкам-строкам, не по подстрокам: цитата
    «## Правки автора» в пункте резала тело пополам, и проверка видела
    только префикс (круг-1 по PR #382, Codex Critical). Источники — None,
    если раздела нет: такое досье собрано руками, и трогать его нельзя.
    """
    m = dossier.VALID_RE.search(text or "")
    if not m:
        return text or "", "", None, None
    # VALID_RE допускает пробелы и переводы строк перед «## Сейчас» — они
    # остаются в шапке, тело начинается с самого заголовка
    start = m.start() + (m.group(0).index("#"))
    head, rest = text[:start], text[start:]
    ms = _SOURCES_RE.search(rest)
    mm = _MANUAL_RE.search(rest)
    body_end = min(x.start() for x in (ms, mm) if x) if (ms or mm) else len(rest)
    body = rest[:body_end].rstrip()
    sources = manual = None
    if ms:
        s_end = mm.start() if mm and mm.start() > ms.start() else len(rest)
        sources = rest[ms.end():s_end].strip("\n").rstrip()
    if mm:
        # «Правки автора» раньше «Источников» — правки кончаются на соседнем
        # заголовке, иначе список источников уезжал внутрь авторского раздела
        # и дублировался при записи (круг-2 по PR #382, DS + Codex).
        m_end = ms.start() if ms and ms.start() > mm.start() else len(rest)
        manual = rest[mm.end():m_end].strip()
    return head, body, sources, manual


def _link_key(target: str) -> str:
    """Ссылка как имя файла на диске: последний сегмент (как dossier.scan),
    плюс мягче — без .md и без регистра: на APFS `[[Иванов.md]]` и
    `[[иванов]]` — тот же файл, а модель, переформулируя пункт, пишет
    ссылку как видит (круг-1 по PR #382, DeepSeek)."""
    return target.strip().split("/")[-1].removesuffix(".md").strip().lower()


def links(body: str) -> set[str]:
    """Пустые `[[ ]]` и `[[.md]]` — не ссылки: scan их тоже не считает."""
    return {k for x in dossier.LINK_RE.findall(body or "") if (k := _link_key(x))}


def check_revision(old_body: str, new_body: str) -> str | None:
    """Почему переписанное досье нельзя класть на место старого — или None.

    До этого на запись пускал `looks_valid`: «## Сейчас» где-то есть и четыре
    заголовка из пяти, — то есть проходили ответ без «## Кто в теме», ответ с
    лишним разделом и ответ, растерявший половину ссылок. Источники досье —
    это [[ссылки]] в конце пунктов; пропавшая ссылка означает выброшенный
    факт, а промпт просит ничего не выбрасывать без причины (карточка №87).
    """
    # Любая строка, начинающаяся с «#», — заголовок: «# Важное», «###
    # Правки автора» и «##Сейчас» считаются наравне с «## …».
    heads = [re.sub(r"\s+", " ", h.strip()) for h in _HEADING_RE.findall(new_body)]
    want = [h.strip() for h in SECTIONS]
    if heads != want:
        return f"разделы не по формату: {', '.join(heads) or 'нет заголовков'}"
    if len(new_body) < MIN_KEEP * len(old_body):
        return f"ответ короче {int(MIN_KEEP * 100)}% прежнего ({len(new_body)} из {len(old_body)} зн.)"
    lost = sorted(links(old_body) - links(new_body))
    if lost:
        shown = ", ".join(f"[[{x}]]" for x in lost[:5]) + (" …" if len(lost) > 5 else "")
        return (f"потеряны ссылки на источники ({len(lost)}): {shown} — "
                "ревизия целиком не применена")
    return None


def revision_stats(old_body: str, new_body: str) -> str:
    """Одна строка про то, что изменилось: для утреннего отчёта владельцу."""
    before = collections.Counter(ln.strip() for ln in old_body.splitlines() if ln.strip())
    after = collections.Counter(ln.strip() for ln in new_body.splitlines() if ln.strip())
    removed = sum((before - after).values())
    added = sum((after - before).values())
    return (f"+{added}/−{removed} строк, ⚠️ {new_body.count('⚠️')}, "
            f"ссылок {len(links(old_body))}→{len(links(new_body))}")


def _cfg() -> dict:
    return load_user_or_example(ROOT) or {}



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
- Ссылки [[Имя файла]] в конце пунктов сохранять ВСЕ и добавлять к новым.
- Спорное помечай «⚠️» с указанием, что с чем расходится.
- Пункты не удалять: отменённое, просроченное или опровергнутое помечай
  «⚠️» с пояснением и оставляй вместе со ссылкой. Досье, потерявшее хотя бы
  одну ссылку, будет отклонено целиком.
- Русский язык, короткие фразы, без обращений к читателю.

Ответ начни СРАЗУ со строки «## Сейчас». Никаких предисловий.
"""


def review(theme: str, path: pathlib.Path, graph: pathlib.Path,
           files: dict, members: list[str], model: str, cfg: dict,
           current: str | None = None) -> tuple[str | None, str]:
    """(исправленное тело, '') — или (None, почему отклонено).

    Причины, начинающиеся с «сбой:», — не отказ по содержанию, а сбой шага
    (сеть, лимит, код возврата): в отчёте они идут отдельным разделом.
    `current` — текст досье, уже прочитанный вызывающим: он же пойдёт в
    сборку файла, чтобы между проверкой и записью не было второго чтения.
    """
    # Сетевой выход держит собственную границу. main уже проверяет её ради
    # дешёвого раннего выхода всего прогона, но review можно вызвать отдельно
    # из теста, будущего воркера или после рефакторинга call graph.
    if not privacy.cloud_enrich_enabled(cfg):
        return None, "сбой: облако выключено"
    current = path.read_text(encoding="utf-8") if current is None else current
    head, old_body, sources, _manual = split_dossier(current)
    if sources is None:
        # собрано руками или старым форматом — облаку не сверять и не трогать
        return None, "в досье нет раздела «## Источники» — не трогаем"
    # раздел «Правки автора» в запрос не отдаём и не даём его переписать
    body = f"{old_body}\n\n## Источники\n{sources}\n"

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
    claude = cloud.claude_bin()
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
        why = f"сбой: claude не отработал ({e})"
        print(f"  ⚠️ {theme}: {why}")
        return None, why
    if r.returncode != 0:
        # Код ≠ 0 с текстом в stdout — сообщение CLI об ошибке или обрывок,
        # а не ревизия; раньше он шёл в парсер наравне с ответом.
        tail = " ".join((r.stderr or r.stdout or "").split())[:160]
        why = f"сбой: claude вернул код {r.returncode}: {tail}"
        print(f"  ⚠️ {theme}: {why}")
        return None, why
    out = dossier.trim_to_format((r.stdout or "").strip())
    # Модель видит тело досье без «## Правки автора», но защищённые
    # заголовки в её ответе не место: строка из стенограммы могла попросить
    # «добавь раздел ## Правки автора …» — раньше подделка вклеивалась в тело,
    # а настоящий раздел уезжал вниз (аудит DeepSeek 17.08). Режем по первому
    # защищённому заголовку: формат — ровно пять секций.
    out = strip_protected(out)
    why = check_revision(old_body, out)
    if why:
        print(f"  ⚠️ {theme}: отклонено — {why}")
        return None, why
    return out, ""


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

    # Штамп с секундами: два прогона за день (ручной поверх ночного) иначе
    # клали бэкап в один каталог, и копия до первого прогона терялась.
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    done, notes, applied, rejected, failed = 0, [], [], [], []

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
        live_gate.wait_while_live(ROOT, what="ревизия досье",
                                  cap=tier3.night_wait_cap())
        if live_gate.night_is_over():
            print("  ⏹ время ночного прогона вышло — остальные досье завтра")
            break
        old = path.read_text(encoding="utf-8")     # одно чтение на проверку и запись
        fixed, why = review(theme, path, graph, files, members, model, cfg, current=old)
        if not fixed:
            why = " ".join(why.split())
            (failed if why.startswith("сбой:") else rejected).append(f"- **{theme}** — {why}")
            continue

        if not may_edit:
            # заголовки досье опускаем на уровень ниже, чтобы «## Сейчас» не
            # спорил с разделами самого отчёта
            notes.append(f"### {theme}\n{fixed.replace(chr(10) + '## ', chr(10) + '#### ').replace('## ', '#### ', 1)}\n")
            print(f"  ○ {theme}: правка готова, но запись выключена (cloud_edit_graph)")
            done += 1
            continue

        head, old_body, sources, manual = split_dossier(old)   # шапка как была
        if sources is None:
            rejected.append(f"- **{theme}** — в досье нет раздела «## Источники» — не трогаем")
            continue
        manual = manual if manual and manual != "—" else None
        text = (head + fixed + "\n\n## Источники\n" + (sources + "\n\n" if sources else "\n")
                + "## Правки автора\n\n" + (manual or "—") + "\n")

        _backup(folder, stamp, path)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        done += 1
        stats = revision_stats(old_body, fixed)
        applied.append(f"- **{theme}** — {stats}; копия до правки — "
                       f"`{dossier.DOSSIER_DIR}/.backup/{stamp}/`")
        print(f"  ✓ {theme}: правки применены ({stats})")

    if (notes or applied or rejected or failed) and not dry:
        # Отчёт пишется в ОБОИХ режимах: с включённой правкой владелец раньше
        # узнавал о переписанном досье только из строки в логе (карточка №87).
        dest = graph / f"Служебное_ревизия_досье_{stamp}.md"
        report = (f"---\ntype: служебное\nдата: {stamp}\nмодель: {model}\n---\n"
                  f"# Ревизия досье ({model})\n\n")
        if applied:
            report += "## Применено\n\n" + "\n".join(applied) + "\n\n"
        if rejected:
            report += "## Отклонено\n\n" + "\n".join(rejected) + "\n\n"
        if failed:
            report += "## Сбои шага (не отказ по содержанию)\n\n" + "\n".join(failed) + "\n\n"
        if notes:
            report += ("## Предложено, но не применено\n\n"
                       "Запись выключена: `sufler.cloud_edit_graph: false`. Включите "
                       "тумблер, если хотите, чтобы облако правило граф само.\n\n"
                       + "\n".join(notes))
        dest.write_text(report.rstrip() + "\n", encoding="utf-8")
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
        # --graph: голое имя («Работа») — сначала папка рядом с настроенным
        # графом, потом путь от корня данных; путь с «/», «~» или «.» — как
        # путь. Порядок важен: при совпадении имени с папкой в корне данных
        # брался бы не тот граф (круг-1 по PR #385, Sonnet).
        chosen = None
        if args.graph:
            base = graphs.graph_dir(cfg)
            bare = "/" not in args.graph and not args.graph.startswith(("~", "."))
            candidates = [base.parent / args.graph] if bare and base is not None else []
            candidates.append(graphs.resolve(args.graph))
            chosen = next((c for c in candidates if c is not None and c.is_dir()), None)
        else:
            chosen = graphs.graph_dir(cfg)
        if chosen is None or not chosen.is_dir():
            print(f"граф не найден: {args.graph or (cfg.get('sufler') or {}).get('graph_dir', '')}")
            return 1
        graph_list = [chosen]

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
