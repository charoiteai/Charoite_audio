#!/usr/bin/env python3
"""Tier 3: ревизия ядер графа — дубли и вложения через эмбеддинги + NLI.

Экстрактор создаёт ядра по названию из каждой встречи, и сквозная тема со
временем расщепляется: «Настройка доступа к API» и «Получение
токена» живут порознь, хроника размазана. Этот скрипт находит такие
пары и наводит порядок.

Почему два уровня. difflib по словам здесь слеп (реальные дубли имеют
word-ratio 0.0 — лексика разная), поэтому кандидатов отбирает bge-m3
(Ollama, батч), а судит каждую пару NLI (src/nli.py). Категории:

  ДУБЛЬ     обоюдное следование ≥ 0.72  → слить: хроника объединяется,
            статус берётся свежий, от дубля остаётся redirect
  ВЛОЖЕНИЕ  одна сторона ≥ 0.85, другая < 0.5 → НЕ сливать: эпизод и
            процесс — осмысленно разные узлы; вписать взаимные ссылки
  ГРАНИЦА   обе ≥ 0.45 → только в отчёт, решает человек

Хаб-фильтр: ядро с генерическим именем («Статус проекта») по NLI
«поглощает» половину графа. Ядро, к которому притянулось ≥ HUB_LIMIT
вложений, считается хабом — его вложения не применяются, только отчёт.

ЗАПУСК ВСЕГДА РУЧНОЙ И ПО УМОЛЧАНИЮ DRY-RUN (только отчёт):
    .venv/bin/python scripts/tier3_cores.py                 # отчёт
    .venv/bin/python scripts/tier3_cores.py --apply         # применить к графу
    .venv/bin/python scripts/tier3_cores.py --graph /путь   # другой граф
Слияние — деструктивная правка живого графа: перед --apply просмотрите
dry-run отчёт. В автопайплайн после встречи это сознательно НЕ встроено.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import nli  # noqa: E402

REPR_LIMIT = 350          # NLI держит 512 токенов на пару — имя+суть с запасом
EMB_PREFILTER = 0.55      # косинус bge-m3; ниже — пары даже не судим
DUP_T = 0.72              # обоюдное следование → дубль
NEST_HI, NEST_LO = 0.85, 0.5
HUB_LIMIT = 3             # ≥ стольких вложений в одно ядро → хаб, не применяем
OLLAMA = "http://127.0.0.1:11434"


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
        essence = sect("Суть") or sect("Задача одной фразой")
        dm = re.search(r"обновлено (\d{4}-\d{2}-\d{2})", status)
        cores.append({
            "path": p, "name": p.stem, "status": status, "essence": essence,
            "chron": re.findall(r"^- \[\[.*", text, re.M), "text": text,
            "date": dm.group(1) if dm else "",
            "repr": f"{p.stem}. {essence}"[:REPR_LIMIT],
        })
    return cores


def embed_all(cores: list[dict]) -> list[list[float]]:
    req = urllib.request.Request(
        f"{OLLAMA}/api/embed",
        data=json.dumps({"model": "bge-m3", "input": [c["repr"] for c in cores],
                         "keep_alive": "60m"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["embeddings"]


def cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5
    db = sum(x * x for x in b) ** 0.5
    return num / (da * db) if da and db else 0.0


def merge(a: dict, b: dict) -> None:
    """Слить дубль: канон — у кого хроника длиннее (при равной — свежий статус)."""
    canon, dup = (a, b) if (len(a["chron"]), a["date"]) >= (len(b["chron"]), b["date"]) else (b, a)
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
    dup["path"].write_text(
        f"---\ntype: ядро\nвид: задача\ntags: [дубль, redirect, tier3-nli]\n---\n"
        f"# {dup['name']} → [[Ядра/{canon['name']}]]\n\n"
        f"⚠️ **Дубль. Смерджен Tier3-NLI.** Хроника перенесена в "
        f"[[Ядра/{canon['name']}|{canon['name']}]].\n",
        encoding="utf-8")
    print(f"  🔀 «{dup['name']}» → «{canon['name']}» (+{len(extra)} строк хроники)")


def link_nested(part: dict, whole: dict) -> None:
    """Вложение: не сливаем, а даём графу взаимные ссылки-подсказки."""
    for src, dst, tag in ((part, whole, "часть более широкой темы"),
                          (whole, part, "частный эпизод этой темы")):
        if f"[[Ядра/{dst['name']}" in src["text"]:
            continue
        src["text"] += f"\n> 🧩 Tier3-NLI: {tag} — [[Ядра/{dst['name']}|{dst['name']}]]\n"
        src["path"].write_text(src["text"], encoding="utf-8")
    print(f"  🧩 связаны: «{part['name']}» ⊂ «{whole['name']}»")


def default_graph() -> pathlib.Path:
    """Папка Ядра рабочего графа — из config.yaml (sufler.graph_dir)."""
    cfg = pathlib.Path(__file__).resolve().parent.parent / "config" / "config.yaml"
    try:
        import yaml
        gd = yaml.safe_load(cfg.read_text(encoding="utf-8"))["sufler"]["graph_dir"]
        return pathlib.Path(gd).expanduser() / "Ядра"
    except Exception:
        return pathlib.Path.cwd() / "Ядра"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", type=pathlib.Path, default=default_graph(),
                    help="папка Ядра графа (default: graph_dir из config.yaml)")
    ap.add_argument("--apply", action="store_true",
                    help="применить изменения (без флага — только отчёт)")
    args = ap.parse_args()

    if not args.graph.is_dir():
        sys.exit(f"нет папки: {args.graph}")
    if not nli.is_available():
        sys.exit("NLI-модель не найдена (models/nli) — Tier3 без неё не работает")

    cores = load_cores(args.graph)
    print(f"ядер: {len(cores)} ({args.graph})")
    embs = embed_all(cores)
    cand = sorted(
        ((cos(embs[i], embs[j]), cores[i], cores[j])
         for i in range(len(cores)) for j in range(i + 1, len(cores))
         if cos(embs[i], embs[j]) >= EMB_PREFILTER),
        key=lambda x: -x[0])
    print(f"пар после эмбеддинг-префильтра (cos≥{EMB_PREFILTER}): {len(cand)}\n")

    dups, nests, border = [], [], []
    for c, a, b in cand:
        ab = nli.entail_prob(a["repr"], b["repr"])
        ba = nli.entail_prob(b["repr"], a["repr"])
        if ab >= DUP_T and ba >= DUP_T:
            dups.append((a, b, c, ab, ba))
        elif max(ab, ba) >= NEST_HI and min(ab, ba) < NEST_LO:
            nests.append((a, b, c, ab, ba))
        elif ab >= 0.45 and ba >= 0.45:
            border.append((a, b, c, ab, ba))

    # хабы: генерическое ядро, к которому NLI притянул пол-графа
    whole_count: dict[str, int] = {}
    for a, b, c, ab, ba in nests:
        whole = b if ab > ba else a
        whole_count[whole["name"]] = whole_count.get(whole["name"], 0) + 1
    hubs = {n for n, k in whole_count.items() if k >= HUB_LIMIT}

    print(f"=== ДУБЛИ (слить): {len(dups)}")
    for a, b, c, ab, ba in dups:
        print(f"  «{a['name']}» ↔ «{b['name']}»  cos {c:.2f}, NLI {ab:.2f}/{ba:.2f}")
    print(f"\n=== ВЛОЖЕНИЯ (связать): {len(nests)}" + (f", хабы не применяются: {hubs}" if hubs else ""))
    for a, b, c, ab, ba in nests:
        part, whole = (a, b) if ab > ba else (b, a)
        mark = "  [ХАБ — пропуск]" if whole["name"] in hubs else ""
        print(f"  «{part['name']}» ⊂ «{whole['name']}»  cos {c:.2f}, NLI {ab:.2f}/{ba:.2f}{mark}")
    print(f"\n=== ГРАНИЦА (решает человек): {len(border)}")
    for a, b, c, ab, ba in border:
        print(f"  «{a['name']}» ?? «{b['name']}»  cos {c:.2f}, NLI {ab:.2f}/{ba:.2f}")

    if not args.apply:
        print("\ndry-run: ничего не изменено. Применить: --apply")
        return
    print("\n--- применяю ---")
    merged: set[str] = set()
    for a, b, c, ab, ba in dups:
        if a["name"] in merged or b["name"] in merged:
            continue
        merge(a, b)
        merged.update((a["name"], b["name"]))
    for a, b, c, ab, ba in nests:
        part, whole = (a, b) if ab > ba else (b, a)
        if whole["name"] in hubs or part["name"] in merged or whole["name"] in merged:
            continue
        link_nested(part, whole)


if __name__ == "__main__":
    main()
