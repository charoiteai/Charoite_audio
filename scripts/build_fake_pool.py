"""Второй ярус пулов подделок маски (`src/pii_fake_pool.py`) из словаря pymorphy3.

Отбор один раз, офлайн (обход словаря — минуты): имена и фамилии в именительном
единственном, только кириллица, склоняются, не общее слово по Navec; детерминированная
выборка. Запуск: `python scripts/build_fake_pool.py > src/pii_fake_pool.py`.
№793: на встрече до 90 людей, первого яруса (`pii_fake.NAMES`) не хватало.
"""
from __future__ import annotations

import pathlib
import random
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import pii_fake  # noqa: E402
import pii_ner   # noqa: E402
import pymorphy3  # noqa: E402

SIZES = {"masc": 800, "femn": 500, "surn": 1500}
DIMINUTIVE = {"masc": r"[ая]$", "femn": r"(ка|ша|ня|ся|уля|юля|юша|уся)$"}
BAD = ("Infr", "Slng", "Erro", "Dist", "Abbr", "Init")


def main() -> None:
    m = pymorphy3.MorphAnalyzer()   # не _load: он сам грузит собираемый модуль
    first = set(pii_fake.NAMES["masc"]) | set(pii_fake.NAMES["femn"]) | set(pii_fake.SURNAMES)
    got: dict[str, set[str]] = {k: set() for k in SIZES}
    for w, tag, _nf, _pid, idx in m.dictionary.iter_known_words():
        if idx or "sing" not in tag or "nomn" not in tag or any(b in tag for b in BAD):
            continue
        w = w.capitalize()
        if not re.fullmatch(r"[А-ЯЁ][а-яё]{3,11}", w) or w in first:
            continue
        if "Name" in tag and ("masc" in tag or "femn" in tag):
            g = "femn" if "femn" in tag else "masc"
            # без уменьшительных («Томка», мужские на -а/-я) и сбоев словаря («Козьмь»);
            # первый разбор слова — имя, а не что-то ещё
            if re.search(DIMINUTIVE[g], w) or re.search(r"[^лр]ь$", w):
                continue
            if "Name" not in m.parse(w)[0].tag:
                continue
            if pii_fake._declines(m, w, "Name", g) and not pii_ner.is_common(w.lower()):
                got[g].add(w)
        elif "Surn" in tag and "masc" in tag and re.search(r"(ов|ев|ёв|ин|ын)$", w):
            if not pii_ner.is_common(w.lower()):
                got["surn"].add(w)
    # слово с приватным маркером (имя владельца внутри другого имени) не идёт в публичный
    # пул: тот же список, что у гейта коммита
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import check_private_markers as cpm
    path = cpm.markers_path()
    if path.exists():
        private = cpm.build_pattern(cpm.load_markers(path))
        got = {k: {w for w in v if not private.search(cpm.nfc(w))} for k, v in got.items()}
    rnd = random.Random(793)
    out = {k: sorted(rnd.sample(sorted(v), min(SIZES[k], len(v)))) for k, v in got.items()}
    print('"""Второй ярус пулов подделок (№793): берётся, когда первый (`pii_fake.NAMES`,\n'
          '`SURNAMES`) занят. Собран `scripts/build_fake_pool.py` из словаря pymorphy3 — руками\n'
          'не править."""\n')
    for k, name in (("masc", "MASC"), ("femn", "FEMN"), ("surn", "SURNAMES")):
        print(f"{name} = (")
        words = out[k]
        for i in range(0, len(words), 10):
            print("    " + " ".join(f'"{w}",' for w in words[i:i + 10]))
        print(")\n")


if __name__ == "__main__":
    main()
