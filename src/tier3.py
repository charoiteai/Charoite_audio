"""Tier 3: ревизия ядер графа — дубли и вложения через bge-m3 + NLI.

Экстрактор создаёт ядра по названию из каждой встречи, и сквозная тема со
временем расщепляется на двойников с размазанной хроникой. Модуль находит
такие пары и наводит порядок. Работает в двух режимах:

- ИНКРЕМЕНТАЛЬНО из graph_updater после каждой встречи: сравниваются только
  ядра, затронутые ЭТОЙ встречей, против всех (O(k×n), десятки секунд даже
  на большом графе) — граф чинится сам, руками ничего запускать не надо.
- ПОЛНЫЙ прогон всех пар — ночная launchd-джоба и CLI scripts/tier3_cores.py.

Почему два уровня отбора. difflib по словам здесь слеп: реальные дубли
(«Настройка доступа к API» ↔ «Получение токена» — одна тема разными словами; бывает и одно имя, по-разному услышанное STT) имеют word-ratio 0.0. Кандидатов отбирает bge-m3 (Ollama, батч),
судит каждую пару NLI (src/nli.py). Категории:

  ДУБЛЬ     обоюдное следование ≥ 0.80 → слить: хроника объединяется,
            статус берётся свежий, от дубля остаётся redirect. Требует
            права apply И написанной человеком «## Суть» хотя бы у одного
            из пары: по автостатусу сравниваются не темы, а сегодняшние
            формулировки — такая пара понижается до пометки
  ДУБЛЬ?    обоюдное 0.72–0.80 → обратимая пометка «возможный дубль»
            в оба файла, сливает человек (осторожный режим автомата)
  ВЛОЖЕНИЕ  одна сторона ≥ 0.85, другая < 0.5 → НЕ сливать: эпизод и
            процесс — осмысленно разные узлы; вписать взаимные ссылки
  ГРАНИЦА   обе ≥ 0.45 → только в отчёт

Страховки автомата: перед любой правкой файлы копируются в
Ядра/.tier3_backup/<дата>/; генерическое ядро-«хаб», к которому NLI
притянул ≥ 3 вложений («Статус проекта»), не трогается автоматически;
нет NLI-модели или Ollama — ревизия молча пропускается, пайплайн живёт.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re
import shutil
import urllib.request

import nli

REPR_LIMIT = 350          # NLI держит 512 токенов на пару — имя+суть с запасом
EMB_PREFILTER = 0.55      # косинус bge-m3; ниже — пары даже не судим
DUP_T = 0.72              # обоюдное следование → похоже на дубль
# ОСТОРОЖНЫЙ РЕЖИМ автомата (cautious mode): деструктивное слияние —
# только при кристальной уверенности с ОБЕИХ сторон; зона DUP_T..MERGE_T
# получает обратимую пометку «возможный дубль» в оба файла, сливает человек.
MERGE_T = 0.80
NEST_HI, NEST_LO = 0.85, 0.5
HUB_LIMIT = 3             # ≥ стольких вложений в одно ядро → хаб, не трогаем
OLLAMA = "http://127.0.0.1:11434"
BACKUP_KEEP = 20          # держим столько последних бэкап-папок


_UPDATED = re.compile(r"_?\(обновлено \d{4}-\d{2}-\d{2}\)_?")


def _plain(status: str) -> str:
    """Статус без служебной метки даты — на вход NLI идёт смысл, не разметка."""
    return " ".join(_UPDATED.sub("", status).split())


def load_cores(folder: pathlib.Path) -> list[dict]:
    cores = []
    for p in sorted(folder.glob("*.md")):
        if p.name.startswith("_"):
            continue
        text = p.read_text(encoding="utf-8")
        if "Дубль. Смерджен" in text:
            continue  # уже сведён прошлой ревизией
        def sect(title: str) -> str:
            m = re.search(rf"## {title}\n(.*?)(?=\n## |\Z)", text, re.S)
            return " ".join(m.group(1).split()) if m else ""
        status = sect("Статус")
        # «## Суть» пишет только человек: upsert_core собирает ядро из
        # «## Статус» и «## Хроники». Без запасного варианта repr сводился к
        # имени файла, и NLI судил пары голых заголовков — ровно та слепота,
        # ради ухода от которой сюда и ставили эмбеддинги с NLI.
        written = sect("Суть") or sect("Задача одной фразой")
        essence = written or _plain(status)
        dm = re.search(r"обновлено (\d{4}-\d{2}-\d{2})", status)
        cores.append({
            "path": p, "name": p.stem, "status": status, "essence": essence,
            # откуда взялась суть: формулировка темы или сегодняшний автостатус.
            # По этому полю revise решает, можно ли пару необратимо сливать
            "essence_src": "секция" if written else "статус",
            "chron": re.findall(r"^- \[\[.*", text, re.M), "text": text,
            "date": dm.group(1) if dm else "",
            "repr": f"{p.stem}. {essence}"[:REPR_LIMIT],
        })
    return cores


def _embed_all(cores: list[dict]) -> list[list[float]]:
    req = urllib.request.Request(
        f"{OLLAMA}/api/embed",
        data=json.dumps({"model": "bge-m3", "input": [c["repr"] for c in cores],
                         "keep_alive": "60m"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["embeddings"]


def _cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5
    db = sum(x * x for x in b) ** 0.5
    return num / (da * db) if da and db else 0.0


def _backup(folder: pathlib.Path, stamp: str, *files: pathlib.Path) -> None:
    """Копия правимых файлов: автомат без бэкапа — не автомат, а рулетка."""
    bdir = folder / ".tier3_backup" / stamp
    bdir.mkdir(parents=True, exist_ok=True)
    for f in files:
        dst = bdir / f.name
        if not dst.exists():
            shutil.copy2(f, dst)
    # старые бэкапы не копим бесконечно
    root = folder / ".tier3_backup"
    old = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.name)
    for d in old[:-BACKUP_KEEP]:
        shutil.rmtree(d, ignore_errors=True)


def _merge(folder: pathlib.Path, stamp: str, a: dict, b: dict, log: list[str]) -> None:
    """Слить дубль: канон — у кого хроника длиннее (при равной — свежий статус)."""
    canon, dup = (a, b) if (len(a["chron"]), a["date"]) >= (len(b["chron"]), b["date"]) else (b, a)
    _backup(folder, stamp, canon["path"], dup["path"])
    text = canon["text"]
    if dup["date"] > canon["date"] and dup["status"]:
        text = re.sub(r"## Статус\n.*?(?=\n## |\Z)",
                      f"## Статус\n{dup['status']}\n\n", text, 1, re.S)
    have = set(canon["chron"])
    extra = [ln for ln in dup["chron"] if ln not in have]
    if extra:
        if "## Хроника" in text:
            text = re.sub(r"(## Хроника\n)", "\\1" + "\n".join(extra) + "\n", text, 1)
        else:
            text += "\n## Хроника\n" + "\n".join(extra) + "\n"
    text += f"\n> 🔀 Tier3-NLI: сюда влита хроника дубля «{dup['name']}».\n"
    canon["path"].write_text(text, encoding="utf-8")
    canon["text"] = text
    dup["path"].write_text(
        f"---\ntype: ядро\nвид: задача\ntags: [дубль, redirect, tier3-nli]\n---\n"
        f"# {dup['name']} → [[Ядра/{canon['name']}]]\n\n"
        f"⚠️ **Дубль. Смерджен Tier3-NLI.** Хроника перенесена в "
        f"[[Ядра/{canon['name']}|{canon['name']}]].\n",
        encoding="utf-8")
    log.append(f"🔀 «{dup['name']}» → «{canon['name']}» (+{len(extra)} строк хроники)")


def _mark_dup(folder: pathlib.Path, stamp: str, a: dict, b: dict, log: list[str]) -> None:
    """Средняя уверенность: обратимая пометка в оба файла, сливает человек."""
    changed = False
    for src, dst in ((a, b), (b, a)):
        if f"возможный дубль: [[Ядра/{dst['name']}" in src["text"]:
            continue
        _backup(folder, stamp, src["path"])
        src["text"] += (f"\n> ⚠️ Tier3-NLI: возможный дубль: "
                        f"[[Ядра/{dst['name']}|{dst['name']}]] — свести вручную, "
                        f"если это одна тема.\n")
        src["path"].write_text(src["text"], encoding="utf-8")
        changed = True
    if changed:
        log.append(f"⚠️ возможный дубль (пометка): «{a['name']}» ↔ «{b['name']}»")


def _link(folder: pathlib.Path, stamp: str, part: dict, whole: dict, log: list[str]) -> None:
    """Вложение: не сливаем, а даём графу взаимные ссылки-подсказки."""
    changed = False
    for src, dst, tag in ((part, whole, "часть более широкой темы"),
                          (whole, part, "частный эпизод этой темы")):
        if f"[[Ядра/{dst['name']}" in src["text"]:
            continue
        _backup(folder, stamp, src["path"])
        src["text"] += f"\n> 🧩 Tier3-NLI: {tag} — [[Ядра/{dst['name']}|{dst['name']}]]\n"
        src["path"].write_text(src["text"], encoding="utf-8")
        changed = True
    if changed:
        log.append(f"🧩 «{part['name']}» ⊂ «{whole['name']}»")


def revise(graph: pathlib.Path, only_names: list[str] | None = None,
           apply: bool = False, mark: bool = False) -> dict:
    """Ревизия ядер графа. only_names — инкрементально (ядра этой встречи).

    Два права, а не одно, потому что цена у правок разная:

    mark  — обратимое: строка-цитата «возможный дубль» / «часть темы» в конец
            файла. Её читает morning_brief в раздел «Tier3 просит свести
            вручную». Без отдельного права выключенный автомат не осторожен,
            а нем: находка живёт только в логе одного прогона.
    apply — необратимое для пользователя: слияние перезаписывает ядро, от
            дубля остаётся redirect-заглушка (оригинал — в .tier3_backup).
            Такое право берут явно: scripts/tier3_cores.py --apply или
            sufler.tier3_auto_apply в конфиге. apply включает и mark.

    Без права на слияние уверенная пара не пропадает, а понижается до
    пометки. Слияние вдобавок требует, чтобы хотя бы у одного ядра суть была
    написана человеком («## Суть»), а не взята из автостатуса: статус
    переписывается на каждой встрече и у двух живых задач одного проекта
    похож сам по себе.

    Возвращает {"dups": [...], "nests": [...], "border": [...], "log": [...]}.
    Любая инфраструктурная беда (нет модели, лежит Ollama) — пустой результат,
    НЕ исключение: ревизия — уборка, она не имеет права валить пайплайн встречи.
    """
    out: dict = {"dups": [], "nests": [], "border": [], "log": []}
    folder = graph / "Ядра"
    if not folder.is_dir() or not nli.is_available():
        return out
    cores = load_cores(folder)
    if len(cores) < 2:
        return out
    focus = ({c["name"] for c in cores} if not only_names
             else {n for n in only_names})
    try:
        embs = _embed_all(cores)
    except Exception:
        return out  # Ollama лежит — не мешаем пайплайну

    pairs = []
    for i in range(len(cores)):
        for j in range(i + 1, len(cores)):
            if cores[i]["name"] not in focus and cores[j]["name"] not in focus:
                continue
            c = _cos(embs[i], embs[j])
            if c >= EMB_PREFILTER:
                pairs.append((c, cores[i], cores[j]))
    pairs.sort(key=lambda x: -x[0])

    dups, maybe_dups, nests = [], [], []
    for c, a, b in pairs:
        try:
            ab = nli.entail_prob(a["repr"], b["repr"])
            ba = nli.entail_prob(b["repr"], a["repr"])
        except Exception:
            continue
        if ab >= MERGE_T and ba >= MERGE_T:
            if a["essence_src"] == "статус" and b["essence_src"] == "статус":
                # сравнивались не темы, а сегодняшние формулировки статуса —
                # на пометку хватает, на перезапись файла нет
                maybe_dups.append((a, b, c, ab, ba))
                out["dups"].append(f"«{a['name']}» ↔? «{b['name']}» {ab:.2f}/{ba:.2f}"
                                   " (суть только из статуса — пометка)")
            else:
                dups.append((a, b, c, ab, ba))
                out["dups"].append(f"«{a['name']}» ↔ «{b['name']}» {ab:.2f}/{ba:.2f}")
        elif ab >= DUP_T and ba >= DUP_T:
            maybe_dups.append((a, b, c, ab, ba))
            out["dups"].append(f"«{a['name']}» ↔? «{b['name']}» {ab:.2f}/{ba:.2f} (пометка)")
        elif max(ab, ba) >= NEST_HI and min(ab, ba) < NEST_LO:
            nests.append((a, b, c, ab, ba))
        elif ab >= 0.45 and ba >= 0.45:
            out["border"].append(f"«{a['name']}» ?? «{b['name']}» {ab:.2f}/{ba:.2f}")

    whole_count: dict[str, int] = {}
    for a, b, c, ab, ba in nests:
        whole = b if ab > ba else a
        whole_count[whole["name"]] = whole_count.get(whole["name"], 0) + 1
    hubs = {n for n, k in whole_count.items() if k >= HUB_LIMIT}
    for a, b, c, ab, ba in nests:
        part, whole = (a, b) if ab > ba else (b, a)
        hub_note = " [хаб — пропуск]" if whole["name"] in hubs else ""
        out["nests"].append(f"«{part['name']}» ⊂ «{whole['name']}» {ab:.2f}/{ba:.2f}{hub_note}")

    if not (apply or mark):
        return out
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    merged: set[str] = set()
    if apply:
        for a, b, c, ab, ba in dups:
            if a["name"] in merged or b["name"] in merged:
                continue
            _merge(folder, stamp, a, b, out["log"])
            merged.update((a["name"], b["name"]))
        to_mark = maybe_dups
    else:
        # права сливать нет — уверенная пара получает ту же обратимую пометку,
        # иначе находка исчезает вместе с логом прогона
        to_mark = dups + maybe_dups
    for a, b, c, ab, ba in to_mark:
        if a["name"] in merged or b["name"] in merged:
            continue
        _mark_dup(folder, stamp, a, b, out["log"])
    for a, b, c, ab, ba in nests:
        part, whole = (a, b) if ab > ba else (b, a)
        if whole["name"] in hubs or part["name"] in merged or whole["name"] in merged:
            continue
        _link(folder, stamp, part, whole, out["log"])
    return out
