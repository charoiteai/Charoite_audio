"""Обновление Obsidian-графа по итогам встречи: сущности, связи, решения.

Запуск: .venv/bin/python src/graph_updater.py [путь_к_стенограмме]
(без аргумента — последняя стенограмма). Вызывается демоном при остановке.
Всё локально: экстракция — gemma через Ollama.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

import requests
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))


def latest_transcript() -> pathlib.Path | None:
    files = [p for p in (ROOT / "transcripts").glob("*.md") if not p.name.endswith("_minutes.md")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def extract(cfg: dict, transcript: str) -> dict | None:
    """LLM → JSON: сущности, связи, решения, темы."""
    r = requests.post(
        cfg["llm"]["base_url"].rstrip("/") + "/api/chat",
        json={
            "model": cfg["llm"]["model"],
            "stream": False,
            "format": "json",
            "options": {"num_ctx": 8192},  # иначе qwen3.6 грузится на 262144 → своп/перезагрузка
            "messages": [
                {"role": "system", "content": (
                    "Ты строишь граф знаний по стенограмме встречи. Верни СТРОГО JSON:\n"
                    '{"название":"2-3 слова, о чём встреча (не больше трёх!)",'
                    '"проект":"строго рабочий проект для ЛЮБОЙ рабочей/рабочей встречи — проект, витрины, '
                    'релизы, данные, инциденты, подрядчики, DAG, LLM-платформа, CRM, GPU: всё это рабочий проект, '
                    'НЕ выдумывай новых рабочих проектов (встреча 20.07 раскололась на два графа). '
                    'Отдельное имя 1-2 слова ТОЛЬКО для явно нерабочих сфер (Семья, Ремонт, Книга)",'
                    '"люди":[{"имя":"...","роль":"...","вклад":"кратко что говорил/решал"}],'
                    '"сущности":[{"имя":"...","тип":"система|проект|команда|документ","суть":"..."}],'
                    '"решения":["..."],"связи":[{"от":"...","к":"...","тип":"..."}],"темы":["..."],'
                    '"ядра":[{"имя":"сквозная тема или задача 2-4 слова (Пилот проект, Оптимизация ресурсов 10%)",'
                    '"тип":"тема|задача","статус":"текущее состояние одной фразой",'
                    '"обновление":"что нового по этому ядру именно на ЭТОЙ встрече",'
                    '"кто":"имя говорящего, чья реплика дала это обновление",'
                    '"время":"время той реплики в формате ЧЧ:ММ, как указано в стенограмме",'
                    '"цитата":"её ДОСЛОВНЫЙ фрагмент, 5-15 слов, скопированный из стенограммы без изменений"}]}\n'
                    "Только то, что реально прозвучало. Имена людей — как звучали (владелец, Дмитрий…). "
                    "Пустые списки допустимы."
                )},
                {"role": "user", "content": f"Стенограмма:\n\n{transcript[:12000]}"},
            ],
        },
        timeout=600,
    )
    raw = r.json().get("message", {}).get("content", "")
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def safe_name(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "-", name).strip()[:60]


ENT_FOLDER = {"система": "Системы", "команда": "Команды", "проект": "Системы",
              "документ": "Системы"}


def canon_link(graph: pathlib.Path, name: str, default_folder: str | None = None) -> str:
    """Вики-ссылка на канонический узел: [[Папка/Имя|Имя]].

    Резолвит в существующий узел (Серёг → Люди/Серёга) тем же find_canonical,
    что и upsert_entity — иначе ссылка в заметке встречи и файл узла расходятся
    и Obsidian плодит фантомные дубли. Узла нет — default_folder, куда его
    создаст upsert_entity; совсем без папки — короткая [[Имя]].
    """
    p = find_canonical(graph, name)
    if p is not None:
        return f"[[{p.parent.name}/{p.stem}|{p.stem}]]"
    disp = safe_name(name)
    if default_folder:
        return f"[[{default_folder}/{disp}|{disp}]]"
    return f"[[{disp}]]"


def find_canonical(graph: pathlib.Path, name: str) -> pathlib.Path | None:
    """Ищет существующий узел: точное имя или имя-подстрока (проект → ИС 1494 проект)."""
    n = safe_name(name).casefold()
    candidates: list[pathlib.Path] = []
    for folder in ("Люди", "Команды", "Системы", "Модели", "Блокеры", "Ядра"):
        d = graph / folder
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            stem = f.stem.casefold()
            if stem == n:
                return f  # точное имя всегда выигрывает (иначе дубль проект возрождался)
            if n in stem or stem in n:
                candidates.append(f)
    return candidates[0] if len(candidates) == 1 else None


def upsert_entity(graph: pathlib.Path, folder: str, name: str, typ: str,
                  desc: str, meeting_link: str, contrib: str):
    canonical = find_canonical(graph, name)
    if canonical is not None:
        p = canonical  # дописываем в существующий узел, а не плодим дубль
    else:
        d = graph / folder
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{safe_name(name)}.md"
    stamp = f"- [[{meeting_link}]] — {contrib}" if contrib else f"- [[{meeting_link}]]"
    if p.exists():
        text = p.read_text(encoding="utf-8")
        if meeting_link in text:
            return
        if "## Встречи" in text:
            text = text.replace("## Встречи", f"## Встречи\n{stamp}", 1)
        else:
            text += f"\n## Встречи\n{stamp}\n"
        p.write_text(text, encoding="utf-8")
    else:
        p.write_text(
            f"---\ntype: {typ}\ntags: [встречи, авто]\n---\n# {name}\n{desc}\n\n"
            f"## Встречи\n{stamp}\n",
            encoding="utf-8",
        )


def core_anchor(core: dict, transcript: str) -> str:
    """Происхождение факта: кто сказал, когда и дословно.

    Без этого хроника обрывается на уровне встречи («что-то решили 21.07»), и
    чтобы понять, чья это была реплика, приходится глазами искать в стенограмме.

    Цитата ПРОВЕРЯЕТСЯ по стенограмме: модель охотно сочиняет правдоподобные
    формулировки, а выдуманная цитата в графе хуже, чем её отсутствие. Сверяем
    по словам (регистр, пунктуация и переносы строк роли не играют).
    """
    who = (core.get("кто") or "").strip().strip(".,!?»«\"")
    when = (core.get("время") or "").strip()
    quote = " ".join((core.get("цитата") or "").split())
    if not quote or len(quote.split()) < 3:
        return ""
    norm = lambda s: " ".join(re.findall(r"[а-яёa-z0-9]+", s.lower()))
    if norm(quote) not in norm(transcript):
        return ""  # цитаты нет в стенограмме — молча отбрасываем выдумку
    head = ", ".join(x for x in (who, when if re.match(r"^\d{1,2}:\d{2}$", when) else "") if x)
    return f" · {head}: «{quote}»" if head else f" · «{quote}»"


def upsert_core(graph: pathlib.Path, core: dict, meeting_link: str, stamp: str,
                transcript: str = ""):
    """Ядро — сквозная тема/задача: статус ПЕРЕЗАПИСЫВАЕТСЯ каждой встречей,
    хроника копится. В графе Obsidian ядра становятся хабами над-уровня."""
    d = graph / "Ядра"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{safe_name(core['имя'])}.md"
    status = (core.get("статус") or "").strip()
    upd = (core.get("обновление") or "").strip()
    anchor = core_anchor(core, transcript) if transcript else ""
    stamp_line = (f"- [[{meeting_link}]] — {upd}{anchor}" if upd
                  else f"- [[{meeting_link}]]{anchor}")
    if p.exists():
        text = p.read_text(encoding="utf-8")
        if status:  # свежий статус вытесняет прежний
            text = re.sub(r"## Статус\n.*?(?=\n## |\Z)",
                          f"## Статус\n{status} _(обновлено {stamp[:10]})_\n\n", text, count=1, flags=re.S)
        if meeting_link not in text:
            if "## Хроника" in text:
                text = text.replace("## Хроника", f"## Хроника\n{stamp_line}", 1)
            else:
                text += f"\n## Хроника\n{stamp_line}\n"
        p.write_text(text, encoding="utf-8")
    else:
        p.write_text(
            f"---\ntype: ядро\nвид: {core.get('тип', 'тема')}\ntags: [ядро, авто]\n---\n"
            f"# {core['имя']}\n\n## Статус\n{status or '—'} _(обновлено {stamp[:10]})_\n\n"
            f"## Хроника\n{stamp_line}\n", encoding="utf-8")


def rebuild_cores_moc(graph: pathlib.Path):
    """Над-уровень: _ЯДРА.md — карта всех ядер со статусами."""
    d = graph / "Ядра"
    if not d.exists():
        return
    lines = ["# Ядра проекта — над-уровень графа\n",
             "Сквозные темы и задачи; статус живёт, хроника копится по встречам.\n"]
    for p in sorted(d.glob("*.md")):
        if p.name.startswith("_"):
            continue
        text = p.read_text(encoding="utf-8")
        m = re.search(r"## Статус\n(.+)", text)
        st = m.group(1).strip() if m else "—"
        lines.append(f"- [[Ядра/{p.stem}|{p.stem}]] — {st}")
    (d / "_ЯДРА.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    cfg = load_cfg()
    tpath = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else latest_transcript()
    if not tpath or not tpath.exists():
        print("нет стенограммы")
        return
    graph_raw = os.environ.get("SUFLER_GRAPH_DIR") or cfg["sufler"].get("graph_dir", "")
    graph = pathlib.Path(graph_raw).expanduser()
    # проверять исходную строку: str(Path("")) == "." — пустой конфиг молча
    # лил бы граф в cwd
    if not graph_raw or not graph.parent.exists():
        print(f"graph_dir не настроен/не существует: {graph}")
        return
    transcript = tpath.read_text(encoding="utf-8")
    minutes_p = tpath.with_name(tpath.stem + "_minutes.md")
    if minutes_p.exists():
        transcript += "\n\n[МИНУТКИ]\n" + minutes_p.read_text(encoding="utf-8")
    if len(transcript) < 300:
        print("стенограмма слишком короткая — граф не трогаем")
        return

    data = extract(cfg, transcript)
    if not data:
        print("LLM не вернула валидный JSON")
        return

    # Мультиграф: каждая сфера — свой граф в iCloud-vault; рабочий дефолт — рабочий проект.
    # SUFLER_GRAPH_DIR (тесты) выбор отключает — путь принудительный.
    project = (data.get("проект") or "").strip()
    if not os.environ.get("SUFLER_GRAPH_DIR") and project and \
            safe_name(project).casefold() not in (graph.name.casefold(), "рабочий проект"):
        graph = graph.parent / safe_name(project)
        if not (graph / "_MOC.md").exists():
            for d in ("Встречи", "Люди", "Системы"):
                (graph / d).mkdir(parents=True, exist_ok=True)
            (graph / "_MOC.md").write_text(
                f"# {project} — MOC\n\n## 🗓 Встречи\n", encoding="utf-8")
            print(f"новый граф проекта: {graph.name}")
        else:
            print(f"граф проекта: {graph.name}")

    m = re.match(r"(\d{4}-\d{2}-\d{2}_\d{4})", tpath.stem)
    stamp = m.group(1) if m else tpath.stem  # только дата-время: 2026-07-17_1040
    already_titled = tpath.stem != stamp  # файл уже переименовывали
    title = (data.get("название") or "").strip()
    # правило 20.07: имя встречи = дата + 2-3 слова, длиннее не бывает
    tw = title.split()[:3]
    while tw and tw[-1].lower() in {"и", "а", "но", "на", "в", "к", "с", "о", "по", "для", "от", "до", "про"}:
        tw.pop()  # обрезка не должна кончаться висящим союзом/предлогом
    title = " ".join(tw).rstrip(",;:")
    if title and not already_titled:  # переименовать логи: дата + о чём общались
        slug = re.sub(r"[,;:!?.]", "", safe_name(title)).replace(" ", "_")[:50]
        new_t = tpath.with_name(f"{stamp}_{slug}.md")
        if not new_t.exists():
            for extra in tpath.parent.glob(f"{stamp}_*.md"):  # _minutes, _hints…
                suffix = extra.name[len(stamp):]  # "_minutes.md"
                extra.rename(extra.with_name(f"{stamp}_{slug}{suffix}"))
            tpath.rename(new_t)
            tpath = new_t
        body = tpath.read_text(encoding="utf-8")
        body = body.replace(f"# Встреча {stamp}", f"# Встреча {stamp} — {title}", 1)
        tpath.write_text(body, encoding="utf-8")
    meeting_link = f"Встречи/{stamp}"
    # 26b на длинных встречах иногда роняет ключи в JSON — битые записи пропускаем,
    # а не валим весь прогон (KeyError на часовой встрече 17.07)
    people = [p for p in (data.get("люди") or []) if isinstance(p, dict) and p.get("имя")
              and p["имя"].strip().lower() not in {"собеседник", "участник", "speaker", "—"}]
    ents = [e for e in (data.get("сущности") or []) if isinstance(e, dict) and e.get("имя")]
    decisions = [d for d in (data.get("решения") or []) if isinstance(d, str) and d.strip()]
    links = [l for l in (data.get("связи") or [])
             if isinstance(l, dict) and l.get("от") and l.get("к")]
    topics = [t for t in (data.get("темы") or []) if isinstance(t, str) and t.strip()]

    # 1) upsert людей и сущностей — ДО заметки встречи, чтобы canon_link
    # резолвил ссылки по уже существующим файлам узлов (иначе фантомные дубли)
    for p in people:
        upsert_entity(graph, "Люди", p["имя"], "person",
                      p.get("роль", ""), meeting_link, p.get("вклад", ""))
    for e in ents:
        upsert_entity(graph, ENT_FOLDER.get(e.get("тип", ""), "Системы"),
                      e["имя"], e.get("тип", "entity"),
                      e.get("суть", ""), meeting_link, "")

    # 1б) ядра: сквозные темы/задачи — статус обновляется, хроника копится
    cores = [c for c in (data.get("ядра") or [])
             if isinstance(c, dict) and c.get("имя")][:4]
    for c in cores:
        upsert_core(graph, c, meeting_link, stamp, transcript)
    if cores:
        rebuild_cores_moc(graph)

    # 2) заметка встречи
    md = [f"---\ntype: встреча\nдата: {stamp}\nтеги: [встреча, авто]"
          + (f"\naliases: [\"{title}\"]" if title else "") + "\n---",
          f"# Встреча {stamp}" + (f" — {title}" if title else ""), ""]
    if topics:
        md += ["## Темы"] + [f"- {t}" for t in topics] + [""]
    if people:
        md += ["## Участники"] + [f"- {canon_link(graph, p['имя'], 'Люди')} — {p.get('роль','')}: {p.get('вклад','')}" for p in people] + [""]
    if ents:
        md += ["## Сущности"] + [f"- {canon_link(graph, e['имя'], ENT_FOLDER.get(e.get('тип',''), 'Системы'))} ({e.get('тип','')}) — {e.get('суть','')}" for e in ents] + [""]
    if cores:
        md += ["## Ядра"] + [f"- {canon_link(graph, c['имя'], 'Ядра')} — {c.get('обновление', c.get('статус', ''))}" for c in cores] + [""]
    if decisions:
        md += ["## Решения"] + [f"- 📌 {d}" for d in decisions] + [""]
    if links:
        md += ["## Связи"] + [f"- {canon_link(graph, l['от'])} → {canon_link(graph, l['к'])}: {l.get('тип','')}" for l in links] + [""]
    md += [f"Стенограмма: `{tpath}`"]
    vdir = graph / "Встречи"
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{stamp}.md").write_text("\n".join(md), encoding="utf-8")

    # 3) строка в MOC
    moc = graph / "_MOC.md"
    if moc.exists():
        text = moc.read_text(encoding="utf-8")
        line = f"- [[{meeting_link}|{title or stamp}]] — {', '.join(topics[:2]) if topics else 'встреча'}"
        if meeting_link not in text:
            if "## 🗓 Встречи" in text:
                text = text.replace("## 🗓 Встречи", f"## 🗓 Встречи\n{line}", 1)
            else:
                text += f"\n## 🗓 Встречи\n{line}\n"
            moc.write_text(text, encoding="utf-8")

    print(f"граф обновлён: встреча {stamp}, людей {len(people)}, сущностей {len(ents)}, решений {len(decisions)}")

    # 3б) решения встречи → память Чароита (ChromaDB/PG, brain :8100), чтобы
    # recall в чате/сессиях знал о встречах, а не только vault_search
    try:
        # 15с: brain ждёт эмбеддинг bge-m3 из Ollama, занятой нашим же extract —
        # 5с не хватало (20.07: «memory недоступна», решения не попали в recall)
        who = ", ".join(p["имя"] for p in people[:6])
        requests.post("http://127.0.0.1:8100/remember", json={
            "text": f"Встреча {stamp} «{title or 'без названия'}» ({who}): темы — "
                    + "; ".join(topics[:4]),
            "category": "learned", "importance": 0.6}, timeout=15)
        for d in decisions[:6]:
            requests.post("http://127.0.0.1:8100/remember", json={
                "text": f"Решение встречи {stamp} «{title}»: {d}",
                "category": "decision", "importance": 0.7}, timeout=15)
        print(f"память Чароита: +{1 + min(len(decisions), 6)} фактов")
    except Exception as e:  # noqa: BLE001 — brain может быть выключен, не валим граф
        print(f"память Чароита недоступна: {e}")

    # 4) пост-встречный разбор: вопросы→ответы, задачи, решения, рекомендации
    try:
        gctx_parts = []
        moc2 = graph / "_MOC.md"
        if moc2.exists():
            gctx_parts.append(moc2.read_text(encoding="utf-8")[:1200])
        for m in sorted((graph / "Встречи").glob("*.md"))[-3:-1]:
            gctx_parts.append(m.read_text(encoding="utf-8")[:800])
        gctx = "\n---\n".join(gctx_parts)[:2500]
        r2 = requests.post(
            cfg["llm"]["base_url"].rstrip("/") + "/api/chat",
            json={
                "model": cfg["llm"]["model"],
                "stream": False,
                "options": {"num_ctx": 8192},  # без него qwen3.6 на 262144 → раздутый KV-кэш
                "messages": [
                    {"role": "system", "content": (
                        # позитивные формулировки вместо «не выдумывай / БЕЗ таблиц»:
                        # локальная модель следует им заметно точнее
                        "Ты аналитик после рабочей встречи. Пиши по-русски, сухо, markdown. "
                        "Опирайся строго на стенограмму и память прошлых встреч; в разделах "
                        "решений и рекомендаций помечай свои варианты словом «предложение». "
                        "Оформляй всё списками «- …» с жирным ключом в начале пункта: "
                        "так документ читается в любом plain-тексте."
                    )},
                    {"role": "user", "content": (
                        (f"Память прошлых встреч (граф):\n{gctx}\n\n" if gctx else "")
                        + f"Стенограмма встречи:\n{transcript[:11000]}\n\n"
                        "Составь разбор строго по разделам:\n"
                        "# Разбор встречи\n"
                        "## Вопросы встречи и ответы\n(каждый прозвучавший вопрос → ответ, если прозвучал; если нет — «открыт»)\n"
                        "## Задачи\n(список «- **Кто** — что — срок»)\n"
                        "## Возможные решения открытых вопросов\n(варианты с плюсами/минусами, кратко)\n"
                        "## Рекомендации: что проработать до следующей встречи\n(конкретные шаги)"
                    )},
                ],
            },
            timeout=600,
        )
        debrief = r2.json().get("message", {}).get("content", "")
        if debrief.strip():
            slug2 = re.sub(r"[,;:!?.]", "", safe_name(title)).replace(" ", "_")[:50] if title else ""
            dpath = tpath.with_name(f"{stamp}_{slug2}_разбор.md" if slug2 else f"{stamp}_разбор.md")
            dpath.write_text(f"<!-- {stamp} · {title or 'встреча'} -->\n" + debrief, encoding="utf-8")
            print(f"разбор: {dpath.name}")
    except Exception as e:
        print(f"разбор не удался: {e}")

    # 4б) артефакты встречи → vault (iCloud): симлинки iCloud не синкает, копируем
    try:
        vdocs = graph / "Документация" / "Стенограммы встреч"
        if vdocs.parent.exists():
            vdocs.mkdir(exist_ok=True)
            import shutil as _sh2
            for f in tpath.parent.glob(f"{stamp}_*.md"):
                _sh2.copy2(f, vdocs / f.name)
            print(f"артефакты скопированы в vault: {vdocs}")
    except Exception as e:  # noqa: BLE001
        print(f"копирование в vault не удалось: {e}")

    # 4в) архив для Finder: папка «дата — название» со всей документацией
    # встречи и ссылкой на граф (Встречи-архив/, ярлык на рабочем столе)
    arch_folder = None
    try:
        from meeting_archive import archive_meeting
        arch_folder = archive_meeting(graph, tpath.parent, stamp, title)
        print(f"архив встречи: {arch_folder.name}")
    except Exception as e:  # noqa: BLE001
        print(f"архив встречи не удался: {e}")

    # 5) уровень 4 — авто-доработка облачным Claude (решение владельца 17.07.2026).
    # Стенограмма уходит в Anthropic API! Выключатель: sufler.cloud_enrich.
    if cfg["sufler"].get("cloud_enrich") and not os.environ.get("SUFLER_NO_CLOUD"):
        try:
            import shutil as _sh
            import subprocess as _sp
            claude_bin = _sh.which("claude") or "/opt/homebrew/bin/claude"
            slug3 = re.sub(r"[,;:!?.]", "", safe_name(title)).replace(" ", "_")[:50] if title else ""
            rev = tpath.with_name(f"{stamp}_{slug3}_ревизия_claude.md" if slug3 else f"{stamp}_ревизия_claude.md")
            prompt = (
                f"Ты — уровень 4 конвейера суфлёра (глубокая доработка после встречи). Работай молча, по-русски.\n"
                f"Файлы встречи (папка {tpath.parent}):\n"
                f"- стенограмма: {tpath.name}\n"
                f"- минутки/подсказки/разбор: тот же префикс, суффиксы _minutes/_hints/_разбор\n"
                f"Obsidian-граф проекта: {graph}\n\n"
                "Задачи:\n"
                f"1. Прочитай стенограмму ПОЛНОСТЬЮ, сверь минутки и разбор: упущенные решения/"
                f"поручения/сроки/цифры, размытые роли, STT-искажения (с расшифровкой). Запиши ревизию в {rev.name} "
                "(в той же папке; если файл есть — дополни).\n"
                "2. Дообогати граф В ОБЕ СТОРОНЫ: (а) от новой встречи — пересечения с прошлыми "
                "встречами и узлами, кросс-ссылки «## Связанные встречи», факты в узлы Люди/Системы; "
                "(б) от старого графа к новой встрече — допиши в её заметку связи, которые видны "
                "только из истории (повторяющиеся люди/системы/блокеры, продолжение тем). "
                "Мерджи очевидные дубли (alias, перенос ссылок). "
                "Не выдумывай — только то, что есть в стенограммах и графе.\n"
                "3. Ничего не удаляй, кроме явных дублей; стенограмму не редактируй. "
                "Формат всех записей: списки «- …» с жирным ключом, БЕЗ markdown-таблиц "
                "(|…|) — их неудобно читать в plain-тексте.\n"
                f"4. В конце скопируй свежие файлы этой встречи (все {stamp}_*.md, включая свою "
                f"ревизию) в {graph}/Документация/Стенограммы встреч/ — перезаписывая старые копии."
                + (f"\n5. Свою ревизию продублируй как '{arch_folder}/Ревизия Claude.md' "
                   "(папка-архив встречи для проводника)." if arch_folder else "")
            )
            log = ROOT / "logs" / f"claude_enrich_{stamp}.log"
            log.parent.mkdir(exist_ok=True)
            # строго через Claude Code по подписке (Max): выкидываем API-ключ из env,
            # чтобы вызов никогда не ушёл на потокенный биллинг Anthropic API
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            try:  # прокси из ~/.claude/settings.json: GUI-запуск без него ловит 403 (регион)
                _s = json.loads((pathlib.Path.home() / ".claude" / "settings.json").read_text(encoding="utf-8"))
                env.update({k: v for k, v in _s.get("env", {}).items() if "proxy" in k.lower()})
            except Exception:  # noqa: BLE001
                pass
            with log.open("w", encoding="utf-8") as lf:
                _sp.Popen(
                    [claude_bin, "-p", prompt,
                     "--model", cfg["sufler"].get("cloud_model", "claude-opus-4-8"),
                     "--allowedTools", "Read,Edit,Write,Grep,Glob",
                     # неразрешённый инструмент в headless = вечный пермишен-запрос
                     "--disallowedTools", "Bash,WebFetch,WebSearch,Task,NotebookEdit,AskUserQuestion",
                     # без пользовательских hooks/MCP — иначе процесс не завершается
                     "--setting-sources", "", "--strict-mcp-config",
                     "--permission-mode", "acceptEdits"],
                    cwd=str(ROOT), env=env, stdin=_sp.DEVNULL,  # не наследовать fifo — claude ждал бы EOF
                    stdout=lf, stderr=_sp.STDOUT, start_new_session=True,
                )
            print(f"cloud-enrich: Claude запущен фоном (лог {log.name})")
        except Exception as e:
            print(f"cloud-enrich не запустился: {e}")


if __name__ == "__main__":
    main()
