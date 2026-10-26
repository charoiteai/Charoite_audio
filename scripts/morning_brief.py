#!/usr/bin/env python3
"""Утренний бриф: готовый контекст дня в графе — ДО первого вопроса.

Sleep-time-подход: ночью (после Tier3-ревизии) собрать из ГОТОВЫХ строк
графа файл `_Сегодня.md` — что решили на последних встречах, какие
поручения и открытые вопросы висят, какие ядра живые, что Tier3 пометил
на сведение. Утром весь рабочий контекст восстанавливается за минуту
чтения, без единого вопроса и без LLM (нечему галлюцинировать, стоимость
нулевая).

Берётся ПОСЛЕДНИЙ день со встречами (после выходных бриф не пустой),
ядра — обновлённые за двое суток от этого дня.

    .venv/bin/python scripts/morning_brief.py                # все графы vault
    .venv/bin/python scripts/morning_brief.py --graph /путь  # один граф
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import graphs  # noqa: E402
import meeting_archive  # noqa: E402
import night_cloud  # noqa: E402
from charoite_graph import redirects  # noqa: E402
from charoite_graph import safe_write  # noqa: E402
from charoite_paths import harden_umask, log_path, resolve_root  # noqa: E402


def sect(text: str, title: str) -> list[str]:
    """Пункты '- ...' из секции '## title' саммари."""
    m = re.search(rf"## {re.escape(title)}\n(.*?)(?=\n## |\n---|\Z)", text, re.S)
    if not m:
        return []
    return [ln.strip() for ln in m.group(1).splitlines() if ln.strip().startswith("- ")]


def sect_any(text: str, key: str) -> list[str]:
    """Секция саммари на любом из языков архива (ru/en/zh + исторические).

    Саммари пишется на языке конфига, а бриф читал только русские заголовки:
    при `sufler.language: en` (или для старых en/zh встреч после смены языка)
    он терял суть, решения, поручения и вопросы (аудит 17.08). Разбор — по
    всем написаниям сразу, как это делает сам архив (`section_names`).
    """
    for name in meeting_archive.section_names(key):
        found = sect(text, name)
        if found:
            return found
    return []


def _graph_health(graph: pathlib.Path, max_age_h: int = 36) -> list[str]:
    """Строки брифа из logs/graph_doctor.json — свежего и про этот граф."""
    path = resolve_root(__file__) / "logs" / "graph_doctor.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        made = dt.datetime.fromisoformat(data.get("generated", ""))
        graphs_ = data["graphs"]
        rep = graphs_.get(str(graph)) or graphs_.get(str(graph.resolve())) or graphs_[graph.name]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if dt.datetime.now() - made > dt.timedelta(hours=max_age_h):
        return []
    out = [f"- отчёт {made:%d.%m %H:%M}: узлов {rep.get('nodes', 0)}, ссылок {rep.get('links', 0)}, "
           f"битых {rep.get('broken', 0)}, сирот {rep.get('orphans', 0)}, "
           f"меток диаризации среди Люди {rep.get('placeholders', 0)}, "
           f"дублей {rep.get('dup_real', 0)}, вне MOC {rep.get('moc_missing', 0)}"]
    out += [f"- ⚠️ {w}" for w in rep.get("warnings", [])]
    return out


#: Исход попытки двери night_cloud → слова для брифа.
_NIGHT_OUTCOMES = {
    "attestation": "аттестация провайдера НЕ прошла",
    "unavailable": "провайдер недоступен",
    "rejected": "ответ не принят",
    "deadline": "ночь кончилась раньше ответа",
    "refused": "провайдер не принят политикой",
    "delivered": "ответ получен, но отчёт не подтверждён",
}
#: Метка впереди часов больше чем на сутки — не «часы ушли назад», а сбой:
#: такая запись не стареет никогда и показывалась бы каждое утро (out3 S-M2).
_FUTURE_H = 24


def _fresh(age: float | None, window_h: float) -> bool:
    return age is not None and -_FUTURE_H <= age <= window_h


def _age_h(stamp, now: dt.datetime) -> float | None:
    """Давность метки двери в часах; нет метки или битая — None (не показываем)."""
    try:
        at = dt.datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.astimezone()
    return (now - at).total_seconds() / 3600


def _night_cloud(graph: pathlib.Path, now: dt.datetime | None = None,
                 max_age_h: int = 36, change_age_h: int = 72) -> list[str]:
    """Строки об аттестации ночного провайдера — только из файла состояния двери.

    Текст отчёта пишет модель, и заголовок «Аттестация» в нём подделывается
    (in3 S-I5, №792); файл состояния пишет сама дверь. Строка — только когда
    есть что сказать: свежая попытка не принята или отпечаток роутера сменился.
    Свежесть — по метке времени, не по календарной дате: ночь через полночь
    и бриф наутро видят ту же попытку (out1 S-I5, DS-I4). Смена отпечатка и
    провал аттестации живут `change_age_h` — пропущенный бриф одного утра и
    следующая попытка их не стирают (out1 H-M2, opus I2).
    """
    # запись уже в форме двери: свободного текста в ней нет (решение главной по out5)
    entry = night_cloud.read_entry(resolve_root(__file__) / "logs" / "night_cloud_state.json",
                                   str(graph.expanduser().resolve()))
    now = now or dt.datetime.now().astimezone()
    out = []
    attempt = entry.get("last_attempt", {})
    age = _age_h(attempt.get("at"), now)
    # давность меньше нуля — часы ушли назад, а не «старая попытка»: показываем (out2 DS-M5)
    shown = _fresh(age, max_age_h) and not attempt.get("accepted")
    if shown:
        # ушёл ли запрос и почему, бриф не говорит: факт и текст — в строке ночного шага
        what = _NIGHT_OUTCOMES.get(attempt.get("outcome"), "исход не записан")
        detail = [f"HTTP {attempt['http_status']}"] if attempt.get("http_status") else []
        if attempt.get("finish_reason") not in (None, "stop"):
            detail.append(f"finish_reason={attempt['finish_reason']}")
        out.append(f"- ⚠️ ночная ревизия ядер ({attempt.get('provider', '?')}) не отработала: "
                   f"{what}" + (f" ({', '.join(detail)})" if detail else ""))
    failure = entry.get("last_attestation_failure", {})
    # та же попытка уже сказана строкой выше — второй раз не повторяем
    same = shown and attempt.get("outcome") == "attestation" and attempt.get("at") == failure.get("at")
    if _fresh(_age_h(failure.get("at"), now), change_age_h) and not same:
        out.append(f"- ⚠️ аттестация ночного провайдера не прошла {failure.get('date') or '?'} "
                   f"(релиз {failure.get('release') or '?'}, отпечаток {failure.get('fingerprint', '')[:16] or '?'})"
                   " — возможна подмена; подробности в логе ночи за эту дату")
    change = entry.get("fingerprint_change", {})
    age = _age_h(change.get("at"), now)
    if _fresh(age, change_age_h):
        out.append(f"- отпечаток роутера Tinfoil сменился: релиз {change.get('from_release') or '?'} "
                   f"({change.get('from') or '?'}) → {change.get('to_release') or '?'} "
                   f"({change.get('to') or '?'})")
    return out


def _bench_alert(graph: pathlib.Path, max_age_h: int = 36) -> list[str]:
    """Строки брифа из logs/memory_bench_alert.json — тревога ночного бенча памяти
    (№629 ч. 2) про этот граф. Номера и категории вопросов, не тексты. Два состояния:
    `alert` — просадка против базы; не стареет: снимает её только прогон, который
    сравнил и просадки не нашёл, или `--accept`, а оборванные ночи `ts` не двигают —
    тогда строка несёт время последнего замера. `unmeasured` — сторож не взведён
    (база не принята или сравнимых вопросов мало), немым он быть не должен; старше
    `max_age_h` не показывается."""
    path = log_path(resolve_root(__file__), "memory_bench_alert")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = list(data.values())
    except (OSError, ValueError, AttributeError):
        return []

    def yes_no(v) -> str:
        return "неизвестно" if v is None else "да" if v else "нет"

    out = []
    now = dt.datetime.now()
    for a in items:
        try:
            made = dt.datetime.fromisoformat(a["ts"])
            if a.get("graph_dir") not in (str(graph), str(graph.resolve())):
                continue
            stale = now - made > dt.timedelta(hours=max_age_h)
            who = f"{a['profile']}/{a['mode']}" if a.get("mode") else a["profile"]
            if a.get("dossiers") is False:
                who += " без досье"     # тревога прогона без оси досье; старая запись без поля — как прежде
            if a.get("state") == "unmeasured":
                if not stale:
                    out.append(f"- ⚠️ бенч памяти ({who}): сторож не взведён — {a['why']}")
                continue
            qs = ", ".join(f"№{q['n']} {q['cat']}" + (f" ({q['why']})" if q.get("why") else "")
                           for q in a["regressed"])
            line = (f"- ⚠️ бенч памяти ({who}): было {a['was']}, стало {a['now']}; ✓→✗ {qs}; "
                    f"HEAD изменился: {yes_no(a['head_changed'])}, "
                    f"граф изменился: {yes_no(a['graph_changed'])}")
            if a.get("dirty"):
                line += "; прогон на незакоммиченном коде"
            if a.get("unmeasured"):
                line += f"; последний прогон не сравним: {a['unmeasured']}"
            if stale:
                hours = int((now - made).total_seconds() // 3600)
                line += f"; последний замер {made:%d.%m %H:%M} — {hours} ч назад"
            if a.get("run"):
                line += f"; итог {a['run']}"
            out.append(line)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def build_brief(graph: pathlib.Path) -> str | None:
    archive = graph / "Встречи-архив"
    cores_dir = graph / "Ядра"
    if not archive.is_dir():
        return None

    # последний день со встречами: папки «YYYY-MM-DD — Название»
    days: dict[str, list[pathlib.Path]] = {}
    for d in archive.iterdir():
        m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[_ ]\d{2}-?\d{2})? — ", d.name)
        if d.is_dir() and m:
            days.setdefault(m.group(1), []).append(d)
    if not days:
        return None
    last_day = max(days)
    meetings = sorted(days[last_day], key=lambda p: p.name)

    lines = [
        "---",
        "type: бриф",
        f"дата: {dt.date.today().isoformat()}",
        "tags: [бриф, авто]",
        "---",
        "",
        f"# Сегодня — контекст дня (по встречам {last_day})",
        "",
    ]

    decided, tasks, open_q = [], [], []
    lines.append("## Последние встречи")
    for mdir in meetings:
        title = mdir.name.split(" — ", 1)[1]
        summary = mdir / "Саммари.md"
        gist = ""
        if summary.exists():
            try:
                text = summary.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""            # битое или исчезнувшее саммари — без пунктов, но бриф выходит (аудит 13.09, DS M4)
            gist = meeting_archive.summary_gist(text) or ""
            decided += [f"{ln}  ·  {title}" for ln in sect_any(text, "decisions")]
            tasks += [f"{ln}  ·  {title}" for ln in sect_any(text, "tasks")]
            open_q += [f"{ln}  ·  {title}" for ln in sect_any(text, "questions")]
        link = f"[[Встречи-архив/{mdir.name}/Саммари|{title}]]"
        lines.append(f"- {link}" + (f" — {gist}" if gist else ""))
    lines.append("")

    for title, items in (("Решили", decided), ("Поручения", tasks),
                         ("Открыто", open_q)):
        if items:
            lines += [f"## {title}"] + items + [""]

    # живые ядра: статус обновлялся в последние 2 суток от дня встреч
    if cores_dir.is_dir():
        cutoff = (dt.date.fromisoformat(last_day) - dt.timedelta(days=1)).isoformat()
        alive, to_merge = [], []
        for p in sorted(cores_dir.glob("*.md")):
            if p.name.startswith("_"):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue             # одно битое ядро не валит весь бриф (аудит 13.09, DS M4)
            if redirects.is_merged(text):
                continue
            sm = re.search(r"## Статус\n(.+)", text)
            status = sm.group(1).strip() if sm else ""
            dm = re.search(r"обновлено (\d{4}-\d{2}-\d{2})", status)
            if dm and dm.group(1) >= cutoff:
                clean = re.sub(r"_\(.*?\)_", "", status).strip()
                alive.append(f"- [[Ядра/{p.stem}|{p.stem}]] — {clean}")
            if "возможный дубль" in text:
                to_merge.append(p.stem)
        if alive:
            lines += ["## Живые ядра"] + alive + [""]
        if to_merge:
            lines += ["## Tier3 просит свести вручную"] + [
                f"- [[Ядра/{n}|{n}]]" for n in sorted(set(to_merge))] + [""]

    # здоровье графа — из ночного graph_doctor (детерминированный линт):
    # только свежий отчёт (до 36 часов) и только по этому графу.
    health = _graph_health(graph) + _bench_alert(graph)
    if health:
        lines += ["## Здоровье графа (ночной doctor)"] + health + [""]
    attest = _night_cloud(graph)
    if attest:
        lines += ["## Аттестация ночного провайдера"] + attest + [""]

    # ночная ревизия Opus: три риска — в самый верх брифа, хвосты — в конец.
    # Берём свежий отчёт (сегодня либо вчера), старый молча пропускаем.
    for shift in (0, 1):
        day = (dt.date.today() - dt.timedelta(days=shift)).isoformat()
        rev = graph / f"Служебное_ночная_ревизия_{day}.md"
        if rev.exists():
            rt = rev.read_text(encoding="utf-8")
            def night_sect(name: str) -> list[str]:
                m = re.search(rf"^#+\s*(?:\d+\.\s*)?{name}.*?$\n(.*?)(?=^#+\s|\Z)",
                              rt, re.M | re.S | re.I)
                return [ln for ln in m.group(1).strip().splitlines() if ln.strip()] if m else []
            risks = night_sect("Три (?:главных )?риска")
            tails = night_sect("Потерянные хвосты")
            # вчерашний отчёт подписан датой: сегодняшней ревизии нет, и брифу
            # не годится выдавать прошлую ночь за эту (№792)
            link = f"[[{rev.stem}|полный отчёт{' за ' + day if shift else ''}]]"
            if risks:
                # вставляем сразу после «# Сегодня …» и пустой строки
                pos = lines.index("", lines.index("# Сегодня — контекст дня (по встречам " + last_day + ")"))
                lines[pos:pos] = ["", "## Три риска недели (ночная ревизия)"] + risks + [link, ""]
            if tails:
                lines += ["## Потерянные хвосты (ночная ревизия)"] + tails + [link, ""]
            break

    return "\n".join(lines)


def main() -> None:
    harden_umask()   # бриф дня собран из встреч — только владельцу (№385)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", type=pathlib.Path, default=None,
                    help="один граф (default: все графы vault с Встречи-архив)")
    args = ap.parse_args()

    found = [args.graph] if args.graph else graphs.all_graphs("Встречи-архив")
    if not found:
        # второй шаг ночной джобы: «графов нет» — не авария, а сообщение.
        # Раньше здесь был iterdir() по несуществующей iCloud-папке, то есть
        # traceback и красный прогон у всех, кто держит Obsidian не там
        print(f"нет графов с папкой «Встречи-архив» — искал в {graphs.where()}")
        return
    for g in found:
        brief = build_brief(g)
        if brief is None:
            print(f"{g.name}: встреч нет — пропуск")
            continue
        out = g / "_Сегодня.md"
        safe_write.write_text(out, brief + "\n")   # обрыв не оставит обрезанный бриф (аудит 13.09, GLM M3)
        print(f"{g.name}: бриф записан ({out})")


if __name__ == "__main__":
    main()
